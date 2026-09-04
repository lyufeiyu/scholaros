from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scholaros import __version__
from scholaros.config import Settings
from scholaros.domain import MAX_RESEARCH_IDEA_LENGTH, ProjectStatus, SearchField, Stage
from scholaros.papers import (
    build_google_scholar_query,
    normalize_http_url,
    search_match_policy,
)
from scholaros.storage import ProjectBusyError
from scholaros.workflow import ResearchWorkflow

logger = logging.getLogger(__name__)
STATIC_ROOT = Path(__file__).with_name("static")


class ProjectCreate(BaseModel):
    idea: str = Field(min_length=8, max_length=MAX_RESEARCH_IDEA_LENGTH)
    sources: list[str] = Field(default_factory=list)
    run_now: bool = True
    guided: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    sources: list[str] | None = None
    limit: int = Field(default=20, ge=1, le=100)
    natural_language: bool = False
    field: SearchField = SearchField.ALL
    author_affiliation: str | None = Field(default=None, min_length=2, max_length=200)
    author_topic: str | None = Field(default=None, min_length=2, max_length=200)
    author_venue: str | None = Field(default=None, min_length=2, max_length=200)


class SearchPlanRevision(BaseModel):
    idea: str | None = Field(default=None, min_length=8, max_length=MAX_RESEARCH_IDEA_LENGTH)


@lru_cache(maxsize=1)
def get_workflow() -> ResearchWorkflow:
    return ResearchWorkflow(Settings.from_env())


def create_app(workflow: ResearchWorkflow | None = None) -> FastAPI:
    app = FastAPI(
        title="ScholarOS",
        version=__version__,
        description="面向研究者的证据追踪、方法设计与写作辅助工作台",
    )
    app.state.workflow = workflow or get_workflow()
    app.state.tasks = set()
    app.state.running_projects = set()
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    def schedule(
        project_id: str,
        *,
        restart: bool = False,
        before_schedule: Callable[[], None] | None = None,
    ) -> bool:
        if project_id in app.state.running_projects:
            return False
        flow: ResearchWorkflow = app.state.workflow
        project_lock = flow.store.project_lock(project_id)
        project_lock.__enter__()
        try:
            project = flow.store.get_project(project_id)
            if project is None:
                raise HTTPException(404, "项目不存在")
            if project.status == ProjectStatus.RUNNING and not restart and before_schedule is None:
                raise HTTPException(409, "上次运行已中断，请从断点继续（/resume），或使用 restart=true 从头重做")
            try:
                flow.validate_sources(project.selected_sources)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if before_schedule is not None:
                before_schedule()
            app.state.running_projects.add(project_id)
            task = asyncio.create_task(flow._run_locked(project_id, restart=restart))
            app.state.tasks.add(task)
        except BaseException:
            app.state.running_projects.discard(project_id)
            project_lock.__exit__(None, None, None)
            raise

        def finished(completed: asyncio.Task[Any]) -> None:
            app.state.tasks.discard(completed)
            app.state.running_projects.discard(project_id)
            try:
                completed.result()
            except asyncio.CancelledError:
                logger.info("ScholarOS 项目 %s 已因服务停止而取消", project_id)
            except Exception:
                logger.exception("ScholarOS 项目 %s 后台运行失败", project_id)
            finally:
                project_lock.__exit__(None, None, None)

        task.add_done_callback(finished)
        return True

    @app.get("/", response_class=HTMLResponse)
    async def home() -> str:
        return (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        flow: ResearchWorkflow = app.state.workflow
        return {
            "status": "ok",
            "version": __version__,
            "model_configured": bool(flow.settings.api_key),
            "model_name": flow.settings.model,
            "model_timeout_seconds": flow.settings.model_timeout,
            "model_thinking": flow.settings.model_thinking,
            "model_streaming": False,
            "sources": flow.search.catalog(),
        }

    @app.get("/api/projects")
    async def list_projects() -> list[dict[str, Any]]:
        flow: ResearchWorkflow = app.state.workflow
        values = []
        for project in flow.store.list_projects():
            value = project.to_dict()
            value["is_active"] = project.id in app.state.running_projects
            values.append(value)
        return values

    @app.post("/api/projects", status_code=201)
    async def create_project(request: ProjectCreate) -> dict[str, Any]:
        flow: ResearchWorkflow = app.state.workflow
        try:
            project = flow.create_project(request.idea, request.sources, guided=request.guided)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        started = False
        if request.run_now:
            try:
                started = schedule(project.id)
            except ProjectBusyError as exc:
                raise HTTPException(409, str(exc)) from exc
        value = project.to_dict()
        value["is_active"] = started
        return value

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str) -> dict[str, Any]:
        flow: ResearchWorkflow = app.state.workflow
        project = flow.store.get_project(project_id)
        if project is None:
            raise HTTPException(404, "项目不存在")
        value = project.to_dict()
        value["is_active"] = project_id in app.state.running_projects
        value["artifacts"] = flow.store.list_artifacts(project_id)
        return value

    @app.post("/api/projects/{project_id}/run", status_code=202)
    async def run_project(project_id: str, restart: bool = False) -> dict[str, str]:
        flow: ResearchWorkflow = app.state.workflow
        project = flow.store.get_project(project_id)
        if project is None:
            raise HTTPException(404, "项目不存在")
        if project_id in app.state.running_projects:
            raise HTTPException(409, "项目正在运行")
        if project.status == ProjectStatus.RUNNING and not restart:
            raise HTTPException(409, "上次运行已中断，请使用 restart=true 重新运行")
        try:
            flow.validate_sources(project.selected_sources)
            started = schedule(project_id, restart=restart)
        except ProjectBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not started:
            raise HTTPException(409, "项目正在运行")
        return {"status": "accepted", "project_id": project_id}

    @app.post("/api/projects/{project_id}/confirm-search", status_code=202)
    async def confirm_search_plan(project_id: str) -> dict[str, str]:
        flow: ResearchWorkflow = app.state.workflow
        if project_id in app.state.running_projects:
            raise HTTPException(409, "项目正在运行")
        try:
            started = schedule(
                project_id,
                before_schedule=lambda: flow._confirm_search_plan_locked(project_id),
            )
        except KeyError as exc:
            raise HTTPException(404, "项目不存在") from exc
        except ProjectBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not started:
            raise HTTPException(409, "项目正在运行")
        return {"status": "accepted", "project_id": project_id}

    def schedule_action(project_id: str, action: Callable[[], None]) -> dict[str, str]:
        try:
            started = schedule(project_id, before_schedule=action)
        except KeyError as exc:
            raise HTTPException(404, "项目不存在") from exc
        except ProjectBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not started:
            raise HTTPException(409, "项目正在运行")
        return {"status": "accepted", "project_id": project_id}

    @app.post("/api/projects/{project_id}/resume", status_code=202)
    async def resume_project(project_id: str) -> dict[str, str]:
        flow: ResearchWorkflow = app.state.workflow
        return schedule_action(project_id, lambda: flow.prepare_resume(project_id))

    @app.post("/api/projects/{project_id}/rerun", status_code=202)
    async def rerun_project(project_id: str, stage: Stage) -> dict[str, str]:
        flow: ResearchWorkflow = app.state.workflow
        return schedule_action(project_id, lambda: flow.prepare_rerun(project_id, stage))

    @app.post("/api/projects/{project_id}/approve", status_code=202)
    async def approve_project(project_id: str) -> dict[str, str]:
        flow: ResearchWorkflow = app.state.workflow
        return schedule_action(project_id, lambda: flow.approve_checkpoint(project_id))

    @app.get("/api/projects/{project_id}/history")
    async def history(project_id: str) -> list[dict[str, Any]]:
        flow: ResearchWorkflow = app.state.workflow
        if flow.store.get_project(project_id) is None:
            raise HTTPException(404, "项目不存在")
        return flow.store.list_history(project_id)

    @app.get("/api/projects/{project_id}/history/{revision}/{name}")
    async def history_artifact(project_id: str, revision: str, name: str) -> FileResponse:
        flow: ResearchWorkflow = app.state.workflow
        path = flow.store.history_artifact_path(project_id, revision, name)
        if path is None:
            raise HTTPException(404, "历史制品不存在")
        return FileResponse(path, filename=name)

    @app.post("/api/projects/{project_id}/reject-search", status_code=202)
    async def reject_search_plan(
        project_id: str, request: SearchPlanRevision
    ) -> dict[str, str]:
        flow: ResearchWorkflow = app.state.workflow
        if project_id in app.state.running_projects:
            raise HTTPException(409, "项目正在运行")
        try:
            started = schedule(
                project_id,
                before_schedule=lambda: flow._reject_search_plan_locked(
                    project_id, revised_idea=request.idea
                ),
            )
        except KeyError as exc:
            raise HTTPException(404, "项目不存在") from exc
        except ProjectBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not started:
            raise HTTPException(409, "项目正在运行")
        return {"status": "accepted", "project_id": project_id}

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str) -> dict[str, str]:
        flow: ResearchWorkflow = app.state.workflow
        if project_id in app.state.running_projects:
            raise HTTPException(409, "项目正在运行，请等待结束或先停止服务")
        try:
            deleted = flow.store.delete_project(project_id)
        except ProjectBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(404, "项目不存在") from exc
        if not deleted:
            raise HTTPException(404, "项目不存在")
        return {"status": "deleted", "project_id": project_id}

    @app.get("/api/projects/{project_id}/events")
    async def project_events(project_id: str, after: int = 0) -> list[dict[str, Any]]:
        flow: ResearchWorkflow = app.state.workflow
        project = flow.store.get_project(project_id)
        if project is None:
            raise HTTPException(404, "项目不存在")
        return flow.store.list_events(project_id, after)

    @app.post("/api/projects/{project_id}/documents", status_code=201)
    async def upload_document(
        project_id: str, file: UploadFile = File(...), role: str = "source"
    ) -> dict[str, Any]:
        flow: ResearchWorkflow = app.state.workflow
        project = flow.store.get_project(project_id)
        if project is None:
            raise HTTPException(404, "项目不存在")
        if project.status == ProjectStatus.RUNNING or project_id in app.state.running_projects:
            raise HTTPException(409, "项目正在运行，不能上传资料")
        if role not in {"source", "results"}:
            raise HTTPException(422, "role 只能是 source 或 results")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in flow.ingestor.allowed_suffixes:
            raise HTTPException(415, "仅支持 PDF、TXT 和 Markdown")
        raw = await file.read(20 * 1024 * 1024 + 1)
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(413, "文件不能超过 20 MiB")
        with tempfile.NamedTemporaryFile(
            dir=flow.settings.home, prefix=f"upload-{project_id}-", suffix=suffix, delete=False
        ) as handle:
            handle.write(raw)
            temp = Path(handle.name)
        try:
            return flow.add_document(project_id, temp, role=role, display_name=file.filename)
        except ProjectBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "项目不存在") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            temp.unlink(missing_ok=True)

    @app.get("/api/projects/{project_id}/artifacts/{name}")
    async def artifact(project_id: str, name: str) -> FileResponse:
        flow: ResearchWorkflow = app.state.workflow
        path = flow.store.artifact_path(project_id, name)
        if path is None:
            raise HTTPException(404, "制品不存在")
        return FileResponse(path, filename=name)

    @app.post("/api/search")
    async def search_papers(request: SearchRequest) -> dict[str, Any]:
        flow: ResearchWorkflow = app.state.workflow
        try:
            if request.sources == []:
                raise ValueError("至少选择一个论文源")
            plan = await flow.prepare_search_plan(
                request.query,
                natural_language=request.natural_language,
                field=request.field,
            )
            result = await flow.search.search(
                plan.search_query,
                limit=request.limit,
                selected=request.sources,
                field=plan.field,
                author_affiliation=request.author_affiliation,
                author_topic=request.author_topic,
                author_venue=request.author_venue,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        plan_value = {
            **plan.to_dict(),
            "matching_policy": search_match_policy(
                plan.field,
                author_affiliation=request.author_affiliation,
                author_topic=request.author_topic,
                author_venue=request.author_venue,
            ),
            "author_filters": {
                "affiliation": request.author_affiliation,
                "topic": request.author_topic,
                "venue": request.author_venue,
            },
            "google_scholar_query": build_google_scholar_query(
                plan.search_query,
                plan.field,
                author_affiliation=request.author_affiliation,
                author_topic=request.author_topic,
                author_venue=request.author_venue,
            ),
        }
        return {
            **plan_value,
            "search_plan": plan_value,
            "papers": [_paper_for_web(paper.to_dict()) for paper in result.papers],
            "failures": [item.to_dict() for item in result.failures],
            "filtered_out": result.filtered_out,
        }

    return app


app = create_app()


def _paper_for_web(value: dict[str, Any]) -> dict[str, Any]:
    """只向浏览器暴露可导航的 HTTP(S) 链接，避免不可信学术元数据注入协议。"""
    result = dict(value)
    for field in ("landing_url", "pdf_url"):
        url = result.get(field)
        result[field] = normalize_http_url(url) if isinstance(url, str) else None
    return result
