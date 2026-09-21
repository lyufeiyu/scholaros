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
from scholaros.delivery import prepare_delivery
from scholaros.domain import (
    MAX_RESEARCH_IDEA_LENGTH,
    Event,
    Paper,
    Project,
    ProjectStatus,
    SearchField,
    SearchQueryPlan,
    Stage,
    utc_now,
)
from scholaros.ingestion import DocumentIngestor
from scholaros.llm import OpenAICompatibleModel
from scholaros.papers import PaperSearchService
from scholaros.review import PaperReviewer
from scholaros.storage import ProjectStore
from scholaros.workspace import (
    DESIGN_CONFIGURATION_FIELDS,
    DRAFT_CONFIGURATION_FIELDS,
    SCOPING_CONFIGURATION_FIELDS,
    SEARCH_CONFIGURATION_FIELDS,
    build_contribution_options,
    build_figure_story,
    build_learning_plan,
    configuration_changes,
    normalize_configuration,
    record_decision,
)
from scholaros.writing import ResearchWriter

ProgressSink = Callable[[str, dict[str, Any]], None]
DELIVERY_OUTPUT_ARTIFACTS = frozenset(
    {
        "paper.docx",
        "paper.tex",
        "paper.pdf",
        "delivery-manifest.json",
        "delivery-package.zip",
    }
)


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
    stage_outputs = {
        Stage.SCOPING: ("spec", "learning_plan", "contribution_options"),
        Stage.SEARCHING: ("papers", "source_failures", "search_filtered_out", "search_queries",
                          "search_skipped",
                          "search_plan_confirmed", "search_confirmation_required", "search_plan_warnings"),
        Stage.SYNTHESIZING: ("evidence",),
        Stage.DESIGNING: ("design", "figure_story"),
        Stage.DRAFTING: (),
        Stage.REVIEWING: ("review",),
        Stage.REVISING: ("final_review",),
    }
    stage_artifacts = {
        Stage.SCOPING: ("learning-plan.json", "contribution-options.json"),
        Stage.SEARCHING: ("papers.json",),
        Stage.SYNTHESIZING: ("evidence.json",),
        Stage.DESIGNING: ("research-design.json", "figure-story.json"),
        Stage.DRAFTING: ("paper-draft.md",), Stage.REVIEWING: ("review.json",),
        Stage.REVISING: (
            "paper.md",
            "final-review.json",
            "paper.docx",
            "paper.tex",
            "paper.pdf",
            "delivery-manifest.json",
            "delivery-package.zip",
        ),
    }
    guided_stages = {Stage.SCOPING, Stage.SYNTHESIZING, Stage.DESIGNING, Stage.DRAFTING}

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
        self.search = search or (
            PaperSearchService([]) if allow_empty_search else PaperSearchService.default(settings)
        )
        model = OpenAICompatibleModel(settings) if settings.api_key and not allow_empty_search else None
        self.writer = writer or ResearchWriter(model)
        self.reviewer = reviewer or PaperReviewer()
        self.ingestor = DocumentIngestor()
        self.progress_sink = progress_sink
        self.allow_empty_search = allow_empty_search

    def create_project(
        self,
        idea: str,
        selected_sources: Sequence[str] | None = None,
        *,
        guided: bool = False,
        configuration: dict[str, Any] | None = None,
    ) -> Project:
        clean_idea = idea.strip()
        if len(clean_idea) < 8:
            raise ValueError("研究想法至少需要 8 个字符")
        if len(clean_idea) > MAX_RESEARCH_IDEA_LENGTH:
            raise ValueError(f"研究想法最多支持 {MAX_RESEARCH_IDEA_LENGTH:,} 个字符")
        sources = list(selected_sources or [])
        self.validate_sources(sources)
        project = Project(
            id=uuid.uuid4().hex[:12],
            idea=clean_idea,
            selected_sources=sources,
            state={
                "schema_version": 2,
                "guided": guided,
                "offline": self.allow_empty_search,
                "configuration": normalize_configuration(configuration),
                "decisions": [],
                "feedback": [],
            },
        )
        self.store.save_project(project)
        self._event(project, "project_created", {"idea": clean_idea})
        return project

    def update_configuration(
        self, project_id: str, configuration: dict[str, Any]
    ) -> Project:
        with self.store.project_lock(project_id):
            project = self._require_project(project_id)
            if project.status == ProjectStatus.RUNNING:
                raise RuntimeError("项目运行中不能修改配置")
            previous = normalize_configuration(project.state.get("configuration"))
            current = normalize_configuration(configuration)
            changed = configuration_changes(previous, current)
            if not changed:
                return project
            if project.state.get("spec"):
                self.store.snapshot_project(project, "修改项目配置")
                if changed & SCOPING_CONFIGURATION_FIELDS:
                    self._invalidate_from(project, Stage.SCOPING)
                elif changed & SEARCH_CONFIGURATION_FIELDS:
                    self._invalidate_from(project, Stage.SEARCHING)
                elif changed & DESIGN_CONFIGURATION_FIELDS:
                    self._invalidate_from(project, Stage.DESIGNING)
                elif changed & DRAFT_CONFIGURATION_FIELDS:
                    self._invalidate_from(project, Stage.DRAFTING)
            project.state.pop("delivery_manifest", None)
            self.store.clear_generated_artifacts(project.id, DELIVERY_OUTPUT_ARTIFACTS)
            project.state["configuration"] = current
            project.state["schema_version"] = 2
            self.store.save_project(project)
            self._event(project, "configuration_updated", {"changed_fields": sorted(changed)})
            return project

    def save_decision(
        self,
        project_id: str,
        *,
        decision_type: str,
        item_id: str,
        value: str,
        comment: str = "",
    ) -> Project:
        with self.store.project_lock(project_id):
            project = self._require_project(project_id)
            if project.status == ProjectStatus.RUNNING:
                raise RuntimeError("项目运行中不能保存选择")
            decisions = record_decision(
                project.state.get("decisions", []),
                decision_type=decision_type,
                item_id=item_id,
                value=value,
                comment=comment,
            )
            if decision_type == "contribution":
                options = project.state.get("contribution_options", [])
                selected = next((item for item in options if item.get("id") == item_id), None)
                if selected is None:
                    raise ValueError("贡献候选不存在，请先完成范围界定")
                if value != "selected":
                    raise ValueError("贡献候选只能标记为 selected")
                spec = project.state.get("spec")
                if isinstance(spec, dict):
                    if project.state.get("evidence") or project.state.get("design"):
                        self.store.snapshot_project(project, "修改贡献方向")
                        self._invalidate_from(project, Stage.SYNTHESIZING)
                    spec["contribution"] = selected["summary"]
                    project.state["selected_contribution"] = item_id
            elif decision_type == "figure":
                story = project.state.get("figure_story", [])
                figure = next((item for item in story if item.get("id") == item_id), None)
                if figure is None:
                    raise ValueError("图件候选不存在，请先完成方法设计")
                if project.state.get("delivery_manifest") or any(
                    self.store.artifact_path(project.id, name) is not None
                    for name in DELIVERY_OUTPUT_ARTIFACTS
                ):
                    self.store.snapshot_project(project, "修改图件选择")
                figure["decision"] = value
                figure["comment"] = comment.strip()
                project.state.pop("delivery_manifest", None)
                self.store.clear_generated_artifacts(project.id, DELIVERY_OUTPUT_ARTIFACTS)
                self.store.save_artifact(
                    project.id,
                    "figure-story.json",
                    json.dumps(story, ensure_ascii=False, indent=2),
                )
            project.state["decisions"] = decisions
            self.store.save_project(project)
            self._event(
                project,
                "decision_saved",
                {"decision_type": decision_type, "item_id": item_id, "value": value},
            )
            return project

    def add_feedback(self, project_id: str, *, scope: str, text: str) -> Project:
        clean_scope = scope.strip()
        if clean_scope not in {"manuscript", "figures", "all"}:
            raise ValueError("反馈范围只能是 manuscript、figures 或 all")
        clean_text = text.strip()
        if not 2 <= len(clean_text) <= 32_000:
            raise ValueError("反馈内容必须为 2—32000 个字符")
        with self.store.project_lock(project_id):
            project = self._require_project(project_id)
            if project.status == ProjectStatus.RUNNING:
                raise RuntimeError("项目运行中不能提交返修意见")
            if project.stage != Stage.COMPLETED or project.state.get(
                "pending_clear_from"
            ) or project.state.get("clear_generated_on_next_run"):
                raise ValueError("项目存在待更新阶段，请先继续运行再提交返修意见")
            paper_path = self.store.artifact_path(project.id, "paper.md")
            if paper_path is None:
                raise ValueError("当前没有可返修的完成稿")
            self.store.snapshot_project(project, "提交返修意见")
            # 返修应从当前完成稿继续，而不是回退到首次生成的初稿；旧版本已进入历史快照。
            self.store.save_artifact(
                project.id, "paper-draft.md", paper_path.read_text(encoding="utf-8")
            )
            project.state.setdefault("feedback", []).append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "scope": clean_scope,
                    "text": clean_text,
                    "status": "pending",
                    "created_at": utc_now(),
                }
            )
            self._invalidate_from(project, Stage.REVISING)
            self.store.save_project(project)
            self._event(project, "feedback_submitted", {"scope": clean_scope})
            return project

    def prepare_delivery(self, project_id: str) -> Project:
        with self.store.project_lock(project_id):
            project = self._require_project(project_id)
            if project.status == ProjectStatus.RUNNING:
                raise RuntimeError("项目运行中不能准备交付包")
            if project.stage != Stage.COMPLETED or project.state.get(
                "pending_clear_from"
            ) or project.state.get("clear_generated_on_next_run"):
                raise ValueError("项目存在待更新阶段，请先继续运行再准备交付包")
            project.state["configuration"] = normalize_configuration(
                project.state.get("configuration")
            )
            project.state["schema_version"] = 2
            manifest = prepare_delivery(project, self.store)
            project.state["delivery_manifest"] = manifest
            self.store.save_project(project)
            self._event(
                project,
                "delivery_prepared",
                {
                    "ready": manifest["ready"],
                    "available_formats": manifest["available_formats"],
                    "missing_formats": manifest["missing_formats"],
                },
            )
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
            if project.state.get("spec"):
                self.store.snapshot_project(project, "添加研究资料")
            documents.append(item)
            if role == "source":
                self._reset_after_source_change(project, documents)
            elif project.state.get("spec") and project.stage in self.stage_order[2:] + [Stage.COMPLETED]:
                self._invalidate_from(project, Stage.SYNTHESIZING)
            self.store.save_project(project)
            self._event(
                project,
                "document_ingested",
                {"name": item["name"], "id": item["id"], "role": role},
            )
            return item
        if existing.get("role", "source") != role:
            self.store.snapshot_project(project, "修改资料角色")
            existing["role"] = role
            self._reset_after_source_change(project, documents)
            self.store.save_project(project)
            self._event(
                project,
                "document_role_updated",
                {"name": existing["name"], "id": existing["id"], "role": role},
            )
        return existing

    def _reset_after_source_change(
        self, project: Project, documents: Sequence[dict[str, Any]]
    ) -> None:
        self.store.clear_generated_artifacts(project.id, DELIVERY_OUTPUT_ARTIFACTS)
        project.state = {
            "documents": list(documents),
            "clear_generated_on_next_run": True,
            "guided": project.state.get("guided", False),
            "offline": project.state.get("offline", False),
            "schema_version": 2,
            "configuration": normalize_configuration(project.state.get("configuration")),
            "decisions": [],
            "feedback": [],
        }
        project.title = None
        project.stage = Stage.SCOPING
        project.status = ProjectStatus.CREATED
        project.error = "参考资料已更新，请重新运行以生成新的范围与检索计划"

    async def run(self, project_id: str, *, restart: bool = False) -> Project:
        with self.store.project_lock(project_id):
            return await self._run_locked(project_id, restart=restart)

    def prepare_resume(self, project_id: str) -> None:
        """调用者必须持有项目锁；获取锁后可确认旧 running 状态已经失活。"""
        project = self._require_project(project_id)
        if project.status == ProjectStatus.RUNNING:
            project.status = ProjectStatus.FAILED
            project.error = "恢复上次中断的步骤"
            self.store.save_project(project)

    async def resume(self, project_id: str) -> Project:
        with self.store.project_lock(project_id):
            self.prepare_resume(project_id)
            return await self._run_locked(project_id)

    def prepare_rerun(self, project_id: str, stage: Stage | str) -> None:
        """持锁后调用；只使选定阶段及其下游失效，保留上游输入。"""
        project = self._require_project(project_id)
        stage = Stage(stage)
        if stage not in self.stage_order:
            raise ValueError("请选择七个研究阶段之一")
        index = self.stage_order.index(stage)
        checkpoint = project.state.get("pending_checkpoint")
        if checkpoint and index > self.stage_order.index(Stage(checkpoint)):
            raise ValueError("请先确认当前阶段，或重做该阶段；不能跳过人工确认")
        if project.state.get("search_confirmation_required") and index > 1:
            raise ValueError("请先确认检索计划，不能跳过外发查询确认")
        if project.stage in self.stage_order and index > self.stage_order.index(project.stage):
            raise ValueError("上游阶段尚未成功完成，请先继续或重做当前阶段")
        for previous in self.stage_order[:index]:
            key = {Stage.SCOPING: "spec", Stage.SEARCHING: "papers",
                   Stage.SYNTHESIZING: "evidence", Stage.DESIGNING: "design",
                   Stage.REVIEWING: "review"}.get(previous)
            if key and key not in project.state:
                raise ValueError(f"缺少上游阶段 {previous.value} 的结果，不能从 {stage.value} 开始")
            for name in self.stage_artifacts[previous]:
                if self.store.artifact_path(project.id, name) is None:
                    raise ValueError(f"缺少上游制品 {name}，请先重做 {previous.value}")
        pending = project.state.get("pending_clear_from")
        if project.state.get("clear_generated_on_next_run") or (
            pending and index > self.stage_order.index(Stage(pending))
        ):
            raise ValueError("上游资料已改变，请先继续当前待运行阶段")
        self.store.snapshot_project(project, f"从 {stage.value} 重做")
        self._invalidate_from(project, stage)
        self.store.save_project(project)

    def _invalidate_from(self, project: Project, stage: Stage) -> None:
        invalid = self.stage_order[self.stage_order.index(stage):]
        for item in invalid:
            for key in self.stage_outputs[item]:
                project.state.pop(key, None)
        project.state.pop("pending_checkpoint", None)
        project.state.pop("delivery_manifest", None)
        self.store.clear_generated_artifacts(project.id, DELIVERY_OUTPUT_ARTIFACTS)
        project.state["completed_stages"] = [
            value for value in project.state.get("completed_stages", []) if value not in invalid
        ]
        project.state["approvals"] = [
            item for item in project.state.get("approvals", []) if item.get("stage") not in invalid
        ]
        project.state["pending_clear_from"] = stage.value
        project.stage = stage
        project.status = ProjectStatus.CREATED
        project.error = None
        if self.stage_order.index(stage) <= self.stage_order.index(Stage.DESIGNING):
            project.state["decisions"] = [
                item
                for item in project.state.get("decisions", [])
                if item.get("type") != "figure"
            ]
        if stage == Stage.SCOPING:
            project.title = None
            project.state.pop("selected_contribution", None)
            project.state["decisions"] = [
                item
                for item in project.state.get("decisions", [])
                if item.get("type") != "contribution"
            ]

    async def rerun_from(self, project_id: str, stage: Stage | str) -> Project:
        with self.store.project_lock(project_id):
            self.prepare_rerun(project_id, stage)
            return await self._run_locked(project_id)

    def approve_checkpoint(self, project_id: str) -> None:
        """持锁后确认已完成的阶段，不修改其输出。"""
        project = self._require_project(project_id)
        pending = project.state.get("pending_checkpoint")
        if not pending or project.status != ProjectStatus.NEEDS_ATTENTION:
            raise ValueError("当前没有待确认的研究阶段")
        project.state.setdefault("approvals", []).append({
            "stage": pending, "confirmed_at": utc_now(),
        })
        project.state.pop("pending_checkpoint")
        project.status = ProjectStatus.CREATED
        project.error = None
        self.store.save_project(project)
        self._event(project, "checkpoint_approved", {"stage": pending})

    async def approve_and_run(self, project_id: str) -> Project:
        with self.store.project_lock(project_id):
            self.approve_checkpoint(project_id)
            return await self._run_locked(project_id)

    async def _run_locked(self, project_id: str, *, restart: bool = False) -> Project:
        project = self._require_project(project_id)
        if project.state.get("offline") and not self.allow_empty_search:
            # 离线项目跨进程恢复时仍保持离线，不改动共享工作流或其他项目的配置。
            offline_flow = ResearchWorkflow(
                self.settings, store=self.store, search=PaperSearchService([]),
                writer=ResearchWriter(), reviewer=self.reviewer,
                progress_sink=self.progress_sink, allow_empty_search=True,
            )
            return await offline_flow._run_locked(project_id, restart=restart)
        self.validate_sources(project.selected_sources)
        if project.status == ProjectStatus.RUNNING and not restart:
            raise RuntimeError("项目已在运行")
        if project.status == ProjectStatus.COMPLETED and not restart:
            return project
        if not restart and (project.state.get("pending_checkpoint") or project.stage == Stage.COMPLETED):
            return project
        clear_generated = restart or bool(
            project.state.get("clear_generated_on_next_run", False)
        )
        if restart:
            self.store.snapshot_project(project, "从头重新运行")
            documents = project.state.get("documents", [])
            project.state = {
                "documents": documents,
                "clear_generated_on_next_run": True,
                "guided": project.state.get("guided", False),
                "offline": project.state.get("offline", False),
                "schema_version": 2,
                "configuration": normalize_configuration(project.state.get("configuration")),
                "decisions": [],
                "feedback": [],
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
            if pending := project.state.get("pending_clear_from"):
                names = frozenset(
                    name for stage in self.stage_order[self.stage_order.index(Stage(pending)):]
                    for name in self.stage_artifacts[stage]
                )
                self.store.clear_generated_artifacts(project.id, names)
                project.state.pop("pending_clear_from")
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
            if not 8 <= len(clean_idea) <= MAX_RESEARCH_IDEA_LENGTH:
                raise ValueError(f"修改后的研究想法必须为 8—{MAX_RESEARCH_IDEA_LENGTH:,} 个字符")
        documents = project.state.get("documents", [])
        self.store.snapshot_project(project, "拒绝检索计划")
        if revised_idea is not None:
            project.idea = clean_idea
        project.state = {
            "documents": documents, "guided": project.state.get("guided", False),
            "offline": project.state.get("offline", False), "schema_version": 2,
            "configuration": normalize_configuration(project.state.get("configuration")),
            "decisions": [], "feedback": [],
            "clear_generated_on_next_run": True,
        }
        project.title = None
        project.stage = Stage.SCOPING
        project.status = ProjectStatus.CREATED
        project.error = None
        self.store.clear_generated_artifacts(project.id, DELIVERY_OUTPUT_ARTIFACTS)
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
                configuration = normalize_configuration(project.state.get("configuration"))
                project.state["configuration"] = configuration
                project.state["learning_plan"] = build_learning_plan(configuration, spec)
                project.state["contribution_options"] = build_contribution_options(spec)
                project.state["selected_contribution"] = "primary"
                self.store.save_artifact(
                    project.id,
                    "learning-plan.json",
                    json.dumps(project.state["learning_plan"], ensure_ascii=False, indent=2),
                )
                self.store.save_artifact(
                    project.id,
                    "contribution-options.json",
                    json.dumps(
                        project.state["contribution_options"], ensure_ascii=False, indent=2
                    ),
                )

            elif stage == Stage.SEARCHING:
                spec = project.state["spec"]
                configuration = normalize_configuration(
                    project.state.get("configuration")
                )
                queries = _workflow_search_queries(spec.get("keywords", []), project.idea)
                venue_query = project.state.get("learning_plan", {}).get("venue_query")
                if venue_query:
                    queries = list(dict.fromkeys([*queries, venue_query]))
                if configuration["research_mode"] == "materials_only":
                    if not project.state.get("documents"):
                        raise RuntimeError("只使用已有材料模式必须先上传至少一份资料或结果")
                    project.state["search_queries"] = []
                    project.state["search_skipped"] = "materials_only"
                    project.state["papers"] = []
                    project.state["source_failures"] = []
                    project.state["search_filtered_out"] = 0
                    self.store.save_artifact(project.id, "papers.json", "[]")
                    self.store.save_project(project)
                else:
                    project.state["search_queries"] = queries
                    has_source_documents = any(
                        item.get("role", "source") == "source"
                        for item in project.state.get("documents", [])
                    )
                    if (
                        (has_source_documents or project.state.get("guided"))
                        and not self.allow_empty_search
                        and not project.state.get("search_plan_confirmed")
                    ):
                        project.state["search_confirmation_required"] = True
                        project.state["search_plan_warnings"] = _search_plan_warnings(
                            queries, str(spec.get("source_basis") or "")
                        )
                        project.status = ProjectStatus.NEEDS_ATTENTION
                        project.error = (
                            "检索计划需要你确认；确认前不会向第三方论文源发送查询"
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
                        limit=min(
                            100,
                            max(
                                24,
                                configuration["same_field_papers"]
                                + configuration["target_venue_papers"],
                            ),
                        ),
                        selected=project.selected_sources or self.search.workflow_sources(),
                    )
                    if not result.papers:
                        discover_papers = getattr(self.writer, "discover_papers", None)
                        if callable(discover_papers) and getattr(self.writer, "model", None) is not None:
                            discovered = await discover_papers("；".join(queries))
                            if discovered:
                                result.papers.extend(discovered)
                    project.state["papers"] = [paper.to_dict() for paper in result.papers]
                    project.state["source_failures"] = [
                        item.to_dict() for item in result.failures
                    ]
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
                project.state["figure_story"] = build_figure_story(
                    project.state.get("configuration", {}),
                    has_results=any(
                        item.get("role") == "results"
                        for item in project.state.get("documents", [])
                    ),
                )
                self.store.save_artifact(
                    project.id,
                    "research-design.json",
                    json.dumps(project.state["design"], ensure_ascii=False, indent=2),
                )
                self.store.save_artifact(
                    project.id,
                    "figure-story.json",
                    json.dumps(project.state["figure_story"], ensure_ascii=False, indent=2),
                )

            elif stage == Stage.DRAFTING:
                documents = self._load_document_text(project)
                configuration = normalize_configuration(project.state.get("configuration"))
                if configuration["workflow"] in {"audit", "review"}:
                    sources = [
                        item.get("text", "")
                        for item in documents
                        if item.get("role", "source") == "source" and item.get("text")
                    ]
                    if not sources:
                        raise RuntimeError("审阅或核查工作必须先上传一份可提取文本的原稿")
                    draft = sources[0]
                else:
                    draft = await self.writer.draft(
                        _spec(project.state["spec"]),
                        _papers(project.state.get("papers", [])),
                        _evidence(project.state.get("evidence", [])),
                        project.state["design"],
                        documents,
                        configuration,
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
                feedback = [
                    item for item in project.state.get("feedback", [])
                    if item.get("status") == "pending"
                ]
                manuscript_feedback = [
                    item for item in feedback if item.get("scope") in {"manuscript", "all"}
                ]
                figure_feedback = [
                    item for item in feedback if item.get("scope") in {"figures", "all"}
                ]
                draft_text = draft_path.read_text(encoding="utf-8")
                revised = (
                    draft_text
                    if feedback and not manuscript_feedback
                    else await self.writer.revise(draft_text, findings, manuscript_feedback)
                )
                figure_feedback_applied = self._apply_figure_feedback(project, figure_feedback)
                for item in feedback:
                    manuscript_applied = (
                        item.get("scope") == "figures" or revised != draft_text
                    )
                    figures_applied = (
                        item.get("scope") == "manuscript"
                        or item.get("id") in figure_feedback_applied
                    )
                    item["status"] = (
                        "applied" if manuscript_applied and figures_applied else "manual_required"
                    )
                    item["components"] = {
                        "manuscript": "applied" if manuscript_applied else "manual_required",
                        "figures": "applied" if figures_applied else "manual_required",
                    }
                    item["processed_at"] = utc_now()
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

            completed = project.state.setdefault("completed_stages", [])
            if stage.value not in completed:
                completed.append(stage.value)
            next_index = self.stage_order.index(stage) + 1
            if next_index < len(self.stage_order):
                project.stage = self.stage_order[next_index]
            if project.state.get("guided") and stage in self.guided_stages:
                project.state["pending_checkpoint"] = stage.value
                project.status = ProjectStatus.NEEDS_ATTENTION
                project.error = f"{stage.value} 已完成，请检查结果后确认继续，或选择该阶段重做"
            self.store.save_project(project)

            self._event(project, "stage_completed", {"stage": stage.value})
            if project.state.get("pending_checkpoint"):
                return

        project.stage = Stage.COMPLETED
        final_passed = bool(project.state.get("final_review", {}).get("passed"))
        unresolved_feedback = any(
            item.get("status") in {"pending", "manual_required"}
            for item in project.state.get("feedback", [])
        )
        project.status = (
            ProjectStatus.COMPLETED
            if final_passed and not unresolved_feedback
            else ProjectStatus.NEEDS_ATTENTION
        )
        self.store.save_project(project)
        self._event(
            project,
            "workflow_completed",
            {"status": project.status.value, "artifacts": self.store.list_artifacts(project.id)},
        )

    def _apply_figure_feedback(
        self, project: Project, feedback: Sequence[dict[str, Any]]
    ) -> set[str]:
        if not feedback:
            return set()
        story = project.state.get("figure_story")
        if not isinstance(story, list) or not story:
            return set()
        changed = False
        for figure in story:
            if not isinstance(figure, dict) or figure.get("decision") == "omit":
                continue
            changed = True
            requests = figure.setdefault("feedback_requests", [])
            requests.extend(
                {
                    "feedback_id": item.get("id"),
                    "text": item.get("text", ""),
                    "recorded_at": utc_now(),
                }
                for item in feedback
            )
            figure["revision_requested"] = True
        self.store.save_artifact(
            project.id,
            "figure-story.json",
            json.dumps(story, ensure_ascii=False, indent=2),
        )
        return {str(item.get("id")) for item in feedback} if changed else set()

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
        configuration = normalize_configuration(project.state.get("configuration"))
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
            kwargs: dict[str, Any] = {"documents": documents}
            if any(parameter.name == "configuration" for parameter in parameters) or has_kwargs:
                kwargs["configuration"] = configuration
            return await scope(project.idea, **kwargs)
        if has_kwargs:
            return await scope(project.idea, documents=documents, configuration=configuration)
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
