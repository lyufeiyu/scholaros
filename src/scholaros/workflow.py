from __future__ import annotations

import asyncio
import inspect
import json
import re
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from scholaros.config import Settings
from scholaros.domain import (
    Event,
    Paper,
    Project,
    ProjectStatus,
    SearchField,
    SearchQueryPlan,
    Stage,
)
from scholaros.ingestion import DocumentIngestor
from scholaros.llm import OpenAICompatibleModel
from scholaros.papers import PaperSearchService
from scholaros.review import PaperReviewer
from scholaros.storage import ProjectStore
from scholaros.writing import ResearchWriter

ProgressSink = Callable[[str, dict[str, Any]], None]


class ResearchWorkflow:
    stage_order = [
        Stage.SCOPING,
        Stage.SEARCHING,
        Stage.SYNTHESIZING,
        Stage.DESIGNING,
        Stage.DRAFTING,
        Stage.REVIEWING,
        Stage.REVISING,
    ]

    def __init__(
        self,
        settings: Settings,
        store: ProjectStore | None = None,
        search: PaperSearchService | None = None,
        writer: ResearchWriter | None = None,
        reviewer: PaperReviewer | None = None,
        progress_sink: ProgressSink | None = None,
        allow_empty_search: bool = False,
    ) -> None:
        self.settings = settings
        self.store = store or ProjectStore(settings)
        self.search = search or PaperSearchService.default(settings)
        model = OpenAICompatibleModel(settings) if settings.api_key else None
        self.writer = writer or ResearchWriter(model)
        self.reviewer = reviewer or PaperReviewer()
        self.ingestor = DocumentIngestor()
        self.progress_sink = progress_sink
        self.allow_empty_search = allow_empty_search

    def create_project(self, idea: str, selected_sources: Sequence[str] | None = None) -> Project:
        clean_idea = idea.strip()
        if len(clean_idea) < 8:
            raise ValueError("研究想法至少需要 8 个字符")
        sources = list(selected_sources or [])
        self.validate_sources(sources)
        project = Project(
            id=uuid.uuid4().hex[:12],
            idea=clean_idea,
            selected_sources=sources,
        )
        self.store.save_project(project)
        self._event(project, "project_created", {"idea": clean_idea})
        return project

    def add_document(
        self,
        project_id: str,
        path: Path,
        *,
        role: str = "source",
        display_name: str | None = None,
    ) -> dict[str, Any]:
        with self.store.project_lock(project_id):
            return self._add_document_locked(
                project_id, path, role=role, display_name=display_name
            )

    def _add_document_locked(
        self,
        project_id: str,
        path: Path,
        *,
        role: str = "source",
        display_name: str | None = None,
    ) -> dict[str, Any]:
        if role not in {"source", "results"}:
            raise ValueError("资料角色只能是 source 或 results")
        project = self._require_project(project_id)
        if project.status == ProjectStatus.RUNNING:
            raise RuntimeError("项目运行中不能上传资料；请等待本轮结束后再上传并重新运行")
        document = self.ingestor.ingest(path)
        text_name = f"source-{document.id}.txt"
        self.store.save_artifact(project.id, text_name, document.text)
        item = document.to_dict()
        item.pop("text")
        if display_name:
            original_name = Path(display_name).name.strip()
            if original_name not in {"", ".", ".."}:
                item["name"] = original_name[:255]
        item["artifact"] = text_name
        item["excerpt"] = document.text[:2000]
        item["role"] = role
        documents = project.state.setdefault("documents", [])
        existing = next(
            (existing for existing in documents if existing["sha256"] == item["sha256"]),
            None,
        )
        if existing is None:
            documents.append(item)
            if role == "source":
                self._reset_after_source_change(project, documents)
            self.store.save_project(project)
            self._event(
                project,
                "document_ingested",
                {"name": item["name"], "id": item["id"], "role": role},
            )
            return item
        if existing.get("role", "source") != role:
            existing["role"] = role
            self._reset_after_source_change(project, documents)
            self.store.save_project(project)
            self._event(
                project,
                "document_role_updated",
                {"name": existing["name"], "id": existing["id"], "role": role},
            )
        return existing

    @staticmethod
    def _reset_after_source_change(
        project: Project, documents: Sequence[dict[str, Any]]
    ) -> None:
        project.state = {
            "documents": list(documents),
            "clear_generated_on_next_run": True,
        }
        project.title = None
        project.stage = Stage.SCOPING
        project.status = ProjectStatus.CREATED
        project.error = "参考资料已更新，请重新运行以生成新的范围与检索计划"

    async def run(self, project_id: str, *, restart: bool = False) -> Project:
        with self.store.project_lock(project_id):
            return await self._run_locked(project_id, restart=restart)

    async def _run_locked(self, project_id: str, *, restart: bool = False) -> Project:
        project = self._require_project(project_id)
        self.validate_sources(project.selected_sources)
        if project.status == ProjectStatus.RUNNING and not restart:
            raise RuntimeError("项目已在运行")
        if project.status == ProjectStatus.COMPLETED and not restart:
            return project
        clear_generated = restart or bool(
            project.state.get("clear_generated_on_next_run", False)
        )
        if restart:
            documents = project.state.get("documents", [])
            project.state = {
                "documents": documents,
                "clear_generated_on_next_run": True,
            }
            project.stage = Stage.SCOPING
        project.status = ProjectStatus.RUNNING
        project.error = None
        self.store.save_project(project)

        try:
            if clear_generated:
                self.store.clear_generated_artifacts(project.id)
                project.state.pop("clear_generated_on_next_run", None)
                self.store.save_project(project)
            await self._run_stages(project)
        except asyncio.CancelledError:
            project.status = ProjectStatus.FAILED
            project.error = "服务停止导致工作流中断，可重新运行"
            self.store.save_project(project)
            self._event(
                project,
                "workflow_cancelled",
                {"stage": project.stage.value, "error": project.error},
            )
            raise
        except Exception as exc:
            project.status = ProjectStatus.FAILED
            project.error = f"{type(exc).__name__}: {exc}"
            self.store.save_project(project)
            self._event(
                project, "workflow_failed", {"stage": project.stage.value, "error": project.error}
            )
            raise
        return project

    def confirm_search_plan(self, project_id: str) -> Project:
        """确认本地资料派生的检索计划，之后才允许向第三方论文源发出请求。"""

        with self.store.project_lock(project_id):
            return self._confirm_search_plan_locked(project_id)

    def _confirm_search_plan_locked(self, project_id: str) -> Project:
        project = self._require_project(project_id)
        if not project.state.get("search_confirmation_required"):
            raise ValueError("该项目当前没有待确认的检索计划")
        queries = project.state.get("search_queries")
        if project.stage != Stage.SEARCHING or not isinstance(queries, list) or not queries:
            raise ValueError("待确认的检索计划状态无效，请重新运行项目")
        project.state["search_confirmation_required"] = False
        project.state["search_plan_confirmed"] = True
        project.error = None
        self.store.save_project(project)
        self._event(project, "search_plan_confirmed", {"queries": queries})
        return project

    async def confirm_search_plan_and_run(self, project_id: str) -> Project:
        with self.store.project_lock(project_id):
            self._confirm_search_plan_locked(project_id)
            return await self._run_locked(project_id)

    def reject_search_plan(self, project_id: str, *, revised_idea: str | None = None) -> Project:
        """拒绝待外发计划，可同时修改研究说明，然后回到范围界定。"""

        with self.store.project_lock(project_id):
            return self._reject_search_plan_locked(project_id, revised_idea=revised_idea)

    def _reject_search_plan_locked(
        self, project_id: str, *, revised_idea: str | None = None
    ) -> Project:
        project = self._require_project(project_id)
        if not project.state.get("search_confirmation_required"):
            raise ValueError("该项目当前没有待拒绝的检索计划")
        if revised_idea is not None:
            clean_idea = revised_idea.strip()
            if len(clean_idea) < 8 or len(clean_idea) > 5_000:
                raise ValueError("修改后的研究想法必须为 8—5000 个字符")
            project.idea = clean_idea
        documents = project.state.get("documents", [])
        project.state = {"documents": documents}
        project.title = None
        project.stage = Stage.SCOPING
        project.status = ProjectStatus.CREATED
        project.error = None
        self.store.save_project(project)
        self._event(project, "search_plan_rejected", {"idea_revised": revised_idea is not None})
        return project

    async def reject_search_plan_and_run(
        self, project_id: str, *, revised_idea: str | None = None
    ) -> Project:
        with self.store.project_lock(project_id):
            self._reject_search_plan_locked(project_id, revised_idea=revised_idea)
            return await self._run_locked(project_id)

    def validate_sources(self, sources: Sequence[str]) -> None:
        restricted = sorted(set(sources) & self.search.workflow_blocked_sources)
        if restricted:
            raise ValueError(
                f"{', '.join(restricted)} 当前仅支持独立检索，不能进入 AI 写作工作流"
            )

    async def prepare_search_plan(
        self,
        query: str,
        *,
        natural_language: bool = False,
        field: SearchField | str = SearchField.ALL,
    ) -> SearchQueryPlan:
        """生成可展示、可审计的检索计划。"""
        clean_query = query.strip()
        if len(clean_query) < 2:
            raise ValueError("检索内容去除空格后至少需要 2 个字符")
        try:
            search_field = SearchField(field)
        except ValueError as exc:
            raise ValueError("检索字段只能是 all、title、author、doi 或 venue") from exc
        if natural_language and search_field != SearchField.ALL:
            raise ValueError("自然语言理解仅用于综合主题检索；标题、作者、DOI 和期刊请原样检索")
        if not natural_language:
            return SearchQueryPlan(
                input_query=clean_query,
                search_query=clean_query,
                search_terms=[clean_query],
                field=search_field,
                natural_language=False,
            )
        try:
            keywords = await self.writer.search_keywords(clean_query)
        except RuntimeError as exc:
            raise ValueError(f"自然语言检索不可用：{exc}") from exc
        return SearchQueryPlan(
            input_query=clean_query,
            search_query=" ".join(keywords),
            search_terms=keywords,
            field=search_field,
            natural_language=True,
        )

    async def prepare_search_query(self, query: str, *, natural_language: bool = False) -> str:
        """兼容旧调用方：只返回最终检索式。"""
        plan = await self.prepare_search_plan(query, natural_language=natural_language)
        return plan.search_query

    async def _run_stages(self, project: Project) -> None:
        start = self.stage_order.index(project.stage) if project.stage in self.stage_order else 0
        for stage in self.stage_order[start:]:
            project.stage = stage
            self.store.save_project(project)
            self._event(project, "stage_started", {"stage": stage.value})

            if stage == Stage.SCOPING:
                spec = await self._scope(project)
                project.title = spec.title
                project.state["spec"] = spec.to_dict()

            elif stage == Stage.SEARCHING:
                spec = project.state["spec"]
                queries = _workflow_search_queries(spec.get("keywords", []), project.idea)
                project.state["search_queries"] = queries
                has_source_documents = any(
                    item.get("role", "source") == "source"
                    for item in project.state.get("documents", [])
                )
                if (
                    has_source_documents
                    and not self.allow_empty_search
                    and not project.state.get("search_plan_confirmed")
                ):
                    project.state["search_confirmation_required"] = True
                    project.state["search_plan_warnings"] = _search_plan_warnings(
                        queries, str(spec.get("source_basis") or "")
                    )
                    project.status = ProjectStatus.NEEDS_ATTENTION
                    project.error = (
                        "上传资料派生的检索计划需要你确认；确认前不会向第三方论文源发送查询"
                    )
                    self.store.save_project(project)
                    self._event(
                        project,
                        "search_plan_confirmation_required",
                        {"title": project.title, "queries": queries},
                    )
                    return
                result = await self.search.search_many(
                    queries,
                    limit=24,
                    selected=project.selected_sources or self.search.workflow_sources(),
                )
                project.state["papers"] = [paper.to_dict() for paper in result.papers]
                project.state["source_failures"] = [item.to_dict() for item in result.failures]
                project.state["search_filtered_out"] = result.filtered_out
                self.store.save_artifact(
                    project.id,
                    "papers.json",
                    json.dumps(project.state["papers"], ensure_ascii=False, indent=2),
                )
                if not result.papers and not self.allow_empty_search:
                    failure_summary = "；".join(
                        f"{item.source}: {item.reason}" for item in result.failures
                    ) or "无来源错误"
                    raise RuntimeError(
                        "没有检索到与研究主题匹配的论文，已停止后续写作以避免领域漂移。"
                        f"实际检索词：{'；'.join(queries)}；"
                        f"严格相关性过滤排除了 {result.filtered_out} 条候选；"
                        f"来源状态：{failure_summary}。请核对资料主题或调整检索词后重新运行"
                    )

            elif stage == Stage.SYNTHESIZING:
                spec = _spec(project.state["spec"])
                papers = _papers(project.state.get("papers", []))
                evidence = await self.writer.synthesize(
                    spec, papers, project.state.get("documents", [])
                )
                project.state["evidence"] = [item.to_dict() for item in evidence]
                self.store.save_artifact(
                    project.id,
                    "evidence.json",
                    json.dumps(project.state["evidence"], ensure_ascii=False, indent=2),
                )

            elif stage == Stage.DESIGNING:
                spec = _spec(project.state["spec"])
                evidence = _evidence(project.state.get("evidence", []))
                project.state["design"] = await self.writer.design(spec, evidence)
                self.store.save_artifact(
                    project.id,
                    "research-design.json",
                    json.dumps(project.state["design"], ensure_ascii=False, indent=2),
                )

            elif stage == Stage.DRAFTING:
                documents = self._load_document_text(project)
                draft = await self.writer.draft(
                    _spec(project.state["spec"]),
                    _papers(project.state.get("papers", [])),
                    _evidence(project.state.get("evidence", [])),
                    project.state["design"],
                    documents,
                )
                self.store.save_artifact(project.id, "paper-draft.md", draft)

            elif stage == Stage.REVIEWING:
                draft_path = self.store.artifact_path(project.id, "paper-draft.md")
                if draft_path is None:
                    raise RuntimeError("缺少论文草稿制品")
                result_sources = [
                    item.get("text", "")
                    for item in self._load_document_text(project)
                    if item.get("role") == "results"
                ]
                review = self.reviewer.review(
                    draft_path.read_text(encoding="utf-8"),
                    _evidence(project.state.get("evidence", [])),
                    result_sources=result_sources,
                )
                project.state["review"] = review.to_dict()
                self.store.save_artifact(
                    project.id,
                    "review.json",
                    json.dumps(review.to_dict(), ensure_ascii=False, indent=2),
                )

            elif stage == Stage.REVISING:
                draft_path = self.store.artifact_path(project.id, "paper-draft.md")
                if draft_path is None:
                    raise RuntimeError("缺少论文草稿制品")
                findings = project.state.get("review", {}).get("findings", [])
                revised = await self.writer.revise(draft_path.read_text(encoding="utf-8"), findings)
                result_sources = [
                    item.get("text", "")
                    for item in self._load_document_text(project)
                    if item.get("role") == "results"
                ]
                final_review = self.reviewer.review(
                    revised,
                    _evidence(project.state.get("evidence", [])),
                    result_sources=result_sources,
                )
                project.state["final_review"] = final_review.to_dict()
                self.store.save_artifact(project.id, "paper.md", revised)
                self.store.save_artifact(
                    project.id,
                    "final-review.json",
                    json.dumps(final_review.to_dict(), ensure_ascii=False, indent=2),
                )

            self.store.save_project(project)
            self._event(project, "stage_completed", {"stage": stage.value})

        project.stage = Stage.COMPLETED
        final_passed = bool(project.state.get("final_review", {}).get("passed"))
        project.status = ProjectStatus.COMPLETED if final_passed else ProjectStatus.NEEDS_ATTENTION
        self.store.save_project(project)
        self._event(
            project,
            "workflow_completed",
            {"status": project.status.value, "artifacts": self.store.list_artifacts(project.id)},
        )

    def _require_project(self, project_id: str) -> Project:
        project = self.store.get_project(project_id)
        if project is None:
            raise KeyError(f"项目不存在：{project_id}")
        return project

    def _load_document_text(self, project: Project) -> list[dict[str, Any]]:
        documents = []
        for item in project.state.get("documents", []):
            value = dict(item)
            path = self.store.artifact_path(project.id, item["artifact"])
            if path is not None:
                value["text"] = path.read_text(encoding="utf-8")
            documents.append(value)
        return documents

    def _event(self, project: Project, event_type: str, payload: dict[str, Any]) -> None:
        self.store.append_event(Event(event_type, project.id, payload))
        if self.progress_sink is not None:
            self.progress_sink(event_type, payload)

    async def _scope(self, project: Project):
        """兼容旧版只接收 idea 的自定义 writer，同时让新版 writer 读取上传资料。"""

        scope = self.writer.scope
        parameters = list(inspect.signature(scope).parameters.values())
        documents = project.state.get("documents", [])
        document_parameter = next(
            (parameter for parameter in parameters if parameter.name == "documents"),
            None,
        )
        has_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
        )
        has_varargs = any(
            parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters
        )
        if document_parameter is not None and document_parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            return await scope(project.idea, documents=documents)
        if has_kwargs:
            return await scope(project.idea, documents=documents)
        if document_parameter is not None or has_varargs:
            return await scope(project.idea, documents)
        return await scope(project.idea)


def _spec(value: dict[str, Any]):
    from scholaros.domain import ResearchSpec

    return ResearchSpec(**value)


def _workflow_search_queries(keywords: Sequence[str], fallback: str) -> list[str]:
    queries = list(
        dict.fromkeys(
            clean
            for keyword in keywords[:3]
            if (clean := " ".join(str(keyword).split()))
        )
    )
    return queries or [" ".join(fallback.split())]


def _search_plan_warnings(queries: Sequence[str], source_basis: str) -> list[str]:
    flagged = [
        query
        for query in queries
        if re.search(r"\b(?:api.?key|password|secret|bearer|token)\b", query, re.I)
    ]
    warnings = []
    if flagged:
        warnings.append(
            "检索词含可能敏感、也可能是合法研究主题的字样，请特别核对后再确认。"
        )
    if re.search(r"[\u3400-\u9fff]", source_basis) and not re.search(
        r"[A-Za-z]", source_basis
    ):
        warnings.append("资料依据为中文、检索词为英文；该跨语言主题映射无法逐字验证，请人工核对。")
    return warnings


def _papers(values: Sequence[dict[str, Any]]) -> list[Paper]:
    return [Paper.from_dict(item) for item in values]


def _evidence(values: Sequence[dict[str, Any]]):
    from scholaros.domain import Evidence

    return [Evidence(**item) for item in values]
