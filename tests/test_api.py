from __future__ import annotations

from fastapi.testclient import TestClient

from scholaros.api import create_app
from scholaros.domain import Paper, ProjectStatus
from scholaros.papers import MemorySource, PaperSearchService, SourceHttpError
from scholaros.workflow import ResearchWorkflow
from scholaros.writing import ResearchWriter


def test_health_and_project_lifecycle(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    with TestClient(create_app(workflow)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["model_configured"] is False
        assert health.json()["model_name"] == "test-model"
        assert health.json()["model_timeout_seconds"] == 300
        assert health.json()["model_thinking"] == "auto"
        assert health.json()["model_streaming"] is False

        created = client.post(
            "/api/projects",
            json={"idea": "设计一个可追踪的科研智能体评估方案", "run_now": False},
        )
        assert created.status_code == 201
        project_id = created.json()["id"]

        fetched = client.get(f"/api/projects/{project_id}")
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "created"
        assert client.get("/docs").status_code == 200


def test_restricted_ieee_source_is_rejected_from_web_workflow(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/projects",
            json={"idea": "验证网页不会把 IEEE 内容直接送入模型", "sources": ["ieee"]},
        )

    assert response.status_code == 422
    assert "仅支持独立检索" in response.json()["detail"]


def test_web_assets_and_document_upload(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    with TestClient(create_app(workflow)) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert 'id="createForm"' in home.text
        assert 'id="searchForm"' in home.text
        assert 'id="naturalLanguageSearch"' in home.text
        assert 'id="googleScholarLink"' in home.text
        assert 'id="searchField"' in home.text
        assert 'id="searchPlan"' in home.text
        assert 'id="authorFilters"' in home.text
        assert 'id="authorAffiliation"' in home.text
        assert 'id="authorTopic"' in home.text
        assert 'id="authorVenue"' in home.text
        assert "留下可核验的证据链" in home.text
        assert "9 类" in home.text
        assert client.get("/static/styles.css").status_code == 200
        script = client.get("/static/app.js")
        assert script.status_code == 200
        assert "async function createProject" in script.text
        assert "async function deleteCurrentProject" in script.text
        assert "async function confirmSearchPlan" in script.text
        assert "async function rejectSearchPlan" in script.text
        assert "/confirm-search" in script.text
        assert "/reject-search" in script.text
        assert "function externalPaperLink" in script.text
        assert "function renderSearchPlan" in script.text
        assert "function updateSearchField" in script.text
        assert "plan.matching_policy" in script.text
        assert "result.google_scholar_query" in script.text
        assert "严格匹配排除" in script.text
        assert "模型已配置 · ${health.model_name}" in script.text
        assert "检索失败，请查看页面中的错误说明。" in script.text
        assert "quality_checks_passed" in script.text
        assert "failure.suggestion" in script.text
        assert "可稍后重试" in script.text
        assert "/run?restart=" in script.text

        created = client.post(
            "/api/projects",
            json={"idea": "验证网页上传和运行按钮的完整交互路径", "run_now": False},
        )
        project_id = created.json()["id"]
        uploaded = client.post(
            f"/api/projects/{project_id}/documents?role=source",
            files={"file": ("notes.md", b"traceable source notes", "text/markdown")},
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["role"] == "source"
        fetched = client.get(f"/api/projects/{project_id}")
        assert fetched.json()["state"]["documents"][0]["name"] == "notes.md"


def test_search_confirmation_endpoint_rejects_project_without_pending_plan(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证网页只能确认实际待处理的论文检索计划")
    with TestClient(create_app(workflow)) as client:
        response = client.post(f"/api/projects/{project.id}/confirm-search")

    assert response.status_code == 422
    assert "没有待确认" in response.json()["detail"]


def test_uploading_source_invalidates_pending_search_plan(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证新增资料会使旧检索计划失效并要求重新运行")
    project.status = ProjectStatus.NEEDS_ATTENTION
    project.state.update(
        {
            "documents": [],
            "search_confirmation_required": True,
            "search_queries": ["old query"],
        }
    )
    workflow.store.save_project(project)

    with TestClient(create_app(workflow)) as client:
        uploaded = client.post(
            f"/api/projects/{project.id}/documents?role=source",
            files={"file": ("new.md", b"new source material", "text/markdown")},
        )
        fetched = client.get(f"/api/projects/{project.id}")

    assert uploaded.status_code == 201
    assert fetched.json()["state"].get("search_confirmation_required") is None
    assert fetched.json()["state"].get("search_queries") is None
    assert fetched.json()["state"].get("spec") is None
    assert fetched.json()["stage"] == "scoping"
    assert fetched.json()["status"] == "created"
    assert "重新运行" in fetched.json()["error"]


def test_project_delete_removes_database_events_and_artifacts(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证删除项目会同时清理状态事件与论文制品")
    artifact = workflow.store.save_artifact(project.id, "paper.md", "# disposable")

    with TestClient(create_app(workflow)) as client:
        response = client.delete(f"/api/projects/{project.id}")
        fetched = client.get(f"/api/projects/{project.id}")

    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "project_id": project.id}
    assert fetched.status_code == 404
    assert workflow.store.list_events(project.id) == []
    assert not artifact.parent.exists()


def test_active_project_delete_is_rejected(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证网页不会删除仍在后台运行的研究项目")
    app = create_app(workflow)

    with TestClient(app) as client:
        app.state.running_projects.add(project.id)
        response = client.delete(f"/api/projects/{project.id}")

    assert response.status_code == 409
    assert workflow.store.get_project(project.id) is not None


def test_cross_process_project_lock_prevents_delete(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证其他进程运行项目时删除接口会安全拒绝请求")

    with workflow.store.project_lock(project.id), TestClient(create_app(workflow)) as client:
        response = client.delete(f"/api/projects/{project.id}")

    assert response.status_code == 409
    assert "另一个 ScholarOS 进程" in response.json()["detail"]
    assert workflow.store.get_project(project.id) is not None


def test_cross_process_project_lock_prevents_run_and_upload(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证运行和上传接口会在返回前检查跨进程项目锁")

    with workflow.store.project_lock(project.id), TestClient(create_app(workflow)) as client:
        run_response = client.post(f"/api/projects/{project.id}/run")
        upload_response = client.post(
            f"/api/projects/{project.id}/documents?role=source",
            files={"file": ("notes.md", b"locked", "text/markdown")},
        )

    assert run_response.status_code == 409
    assert upload_response.status_code == 409
    assert workflow.store.get_project(project.id).status == ProjectStatus.CREATED


def test_run_rechecks_project_after_acquiring_lock(settings, monkeypatch) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证任务拿到项目锁后会重新确认项目是否仍然存在")
    original_get = workflow.store.get_project
    calls = 0

    def disappear_after_first_read(project_id):
        nonlocal calls
        calls += 1
        return original_get(project_id) if calls == 1 else None

    monkeypatch.setattr(workflow.store, "get_project", disappear_after_first_read)
    with TestClient(create_app(workflow)) as client:
        response = client.post(f"/api/projects/{project.id}/run")

    assert response.status_code == 404
    assert calls == 2


def test_upload_reports_not_found_when_project_is_deleted_during_read(settings, monkeypatch) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证读取上传文件期间项目被删除时返回项目不存在")

    def missing_project(*_args, **_kwargs):
        raise KeyError(project.id)

    monkeypatch.setattr(workflow, "add_document", missing_project)
    with TestClient(create_app(workflow)) as client:
        response = client.post(
            f"/api/projects/{project.id}/documents?role=source",
            files={"file": ("notes.md", b"deleted", "text/markdown")},
        )

    assert response.status_code == 404


def test_malformed_project_id_delete_is_not_found(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    with TestClient(create_app(workflow)) as client:
        response = client.delete("/api/projects/not-a-project")

    assert response.status_code == 404


def test_upload_is_rejected_while_web_workflow_runs(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证网页运行期间不会发生资料覆盖问题")
    project.status = ProjectStatus.RUNNING
    workflow.store.save_project(project)

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            f"/api/projects/{project.id}/documents?role=results",
            files={"file": ("results.md", b"real results", "text/markdown")},
        )

    assert response.status_code == 409
    assert "不能上传资料" in response.json()["detail"]


def test_stale_running_project_can_be_explicitly_restarted(settings) -> None:
    class RecordingWorkflow(ResearchWorkflow):
        def __init__(self):
            super().__init__(settings, search=PaperSearchService([]))
            self.restart_values = []

        async def _run_locked(self, project_id: str, *, restart: bool = False):
            self.restart_values.append(restart)
            return self.store.get_project(project_id)

    workflow = RecordingWorkflow()
    project = workflow.create_project("验证网页重新运行按钮能够保留资料并从头执行")
    project.status = ProjectStatus.RUNNING
    workflow.store.save_project(project)

    with TestClient(create_app(workflow)) as client:
        fetched = client.get(f"/api/projects/{project.id}")
        without_restart = client.post(f"/api/projects/{project.id}/run")
        response = client.post(f"/api/projects/{project.id}/run?restart=true")
        client.get("/health")

    assert fetched.json()["is_active"] is False
    assert without_restart.status_code == 409
    assert response.status_code == 202
    assert workflow.restart_values == [True]


def test_search_api_removes_unsafe_metadata_links(settings) -> None:
    source = MemorySource(
        [
            Paper(
                title="Untrusted metadata",
                authors=[],
                year=2026,
                abstract="",
                sources=["memory"],
                external_id="unsafe",
                landing_url="javascript:alert(1)",
                pdf_url="http://[",
            )
        ]
    )
    workflow = ResearchWorkflow(settings, search=PaperSearchService([source]))

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/search",
            json={"query": "metadata", "sources": ["memory"], "limit": 1},
        )

    assert response.status_code == 200
    assert response.json()["papers"][0]["landing_url"] is None
    assert response.json()["papers"][0]["pdf_url"] is None


def test_search_api_interprets_natural_language_and_returns_actual_query(settings) -> None:
    class KeywordWriter(ResearchWriter):
        async def search_keywords(self, query: str) -> list[str]:
            return ["multi-agent systems", "citation reliability"]

    workflow = ResearchWorkflow(
        settings, search=PaperSearchService([]), writer=KeywordWriter()
    )

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/search",
            json={
                "query": "多智能体科研助手的引用可靠性",
                "natural_language": True,
                "limit": 5,
            },
        )

    assert response.status_code == 200
    assert response.json()["input_query"] == "多智能体科研助手的引用可靠性"
    assert "multi-agent systems" in response.json()["search_query"]
    assert response.json()["search_terms"] == [
        "multi-agent systems",
        "citation reliability",
    ]
    assert response.json()["search_plan"]["search_query"] == response.json()["search_query"]


def test_search_api_supports_author_field(settings) -> None:
    source = MemorySource(
        [
            Paper(
                title="Deep learning foundations",
                authors=["Geoffrey Hinton"],
                year=2015,
                abstract="",
                sources=["memory"],
                external_id="author-paper",
            )
        ]
    )
    workflow = ResearchWorkflow(settings, search=PaperSearchService([source]))

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/search",
            json={
                "query": "Geoffrey Hinton",
                "field": "author",
                "sources": ["memory"],
            },
        )

    assert response.status_code == 200
    assert response.json()["field"] == "author"
    assert response.json()["papers"][0]["authors"] == ["Geoffrey Hinton"]
    assert "姓名严格匹配" in response.json()["matching_policy"]
    assert response.json()["search_plan"]["matching_policy"] == (
        response.json()["matching_policy"]
    )
    assert response.json()["filtered_out"] == 0


def test_search_api_supports_author_affiliation_topic_and_venue_filters(settings) -> None:
    source = MemorySource(
        [
            Paper(
                title="Reliable Vision Agents",
                authors=["Wei Wang"],
                year=2026,
                abstract="Computer vision agents.",
                sources=["memory"],
                external_id="target",
                venue="CVPR",
                author_affiliations={"Wei Wang": ["Shenzhen University"]},
            ),
            Paper(
                title="Reliable Vision Agents",
                authors=["Wei Wang"],
                year=2026,
                abstract="Computer vision agents.",
                sources=["memory"],
                external_id="namesake",
                venue="CVPR",
                author_affiliations={"Wei Wang": ["Other University"]},
            ),
        ]
    )
    workflow = ResearchWorkflow(settings, search=PaperSearchService([source]))

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/search",
            json={
                "query": "Wei Wang",
                "field": "author",
                "sources": ["memory"],
                "author_affiliation": "Shenzhen University",
                "author_topic": "vision agents",
                "author_venue": "CVPR",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert [paper["external_id"] for paper in body["papers"]] == ["target"]
    assert body["author_filters"] == {
        "affiliation": "Shenzhen University",
        "topic": "vision agents",
        "venue": "CVPR",
    }
    assert "同一作者" in body["matching_policy"]
    assert '"Wei Wang" "Shenzhen University"' in body["google_scholar_query"]


def test_search_api_rejects_author_filters_for_other_fields(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/search",
            json={
                "query": "vision agents",
                "field": "all",
                "author_affiliation": "Shenzhen University",
            },
        )

    assert response.status_code == 422
    assert "仅能用于作者检索" in response.json()["detail"]


def test_search_api_returns_actionable_source_failure(settings) -> None:
    class RateLimitedSource:
        name = "semantic_scholar"
        available = True

        async def search(self, query, limit, field="all"):
            raise SourceHttpError(429, '{"message":"Too Many Requests"}', 31)

    workflow = ResearchWorkflow(
        settings, search=PaperSearchService([RateLimitedSource()])
    )

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/search",
            json={"query": "research agents", "sources": ["semantic_scholar"]},
        )

    assert response.status_code == 200
    failure = response.json()["failures"][0]
    assert failure["reason"] == "请求频率超过该来源限额（HTTP 429）"
    assert "SEMANTIC_SCHOLAR_API_KEY" in failure["suggestion"]
    assert failure["retryable"] is True
    assert failure["status_code"] == 429
    assert failure["retry_after_seconds"] == 31


def test_search_api_rejects_explicit_empty_source_selection(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/search",
            json={"query": "research agents", "sources": []},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "至少选择一个论文源"


def test_search_api_rejects_malformed_doi(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/search",
            json={"query": "not-a-doi", "field": "doi"},
        )

    assert response.status_code == 422
    assert "有效 DOI" in response.json()["detail"]


def test_search_api_rejects_blank_query_and_unavailable_natural_language(settings) -> None:
    workflow = ResearchWorkflow(
        settings, search=PaperSearchService([]), writer=ResearchWriter()
    )

    with TestClient(create_app(workflow)) as client:
        blank = client.post("/api/search", json={"query": "  "})
        natural = client.post(
            "/api/search",
            json={"query": "珊瑚白化与海水温度变化", "natural_language": True},
        )

    assert blank.status_code == 422
    assert natural.status_code == 422
    assert "未配置可用模型" in natural.json()["detail"]


def test_natural_language_model_network_failure_is_reported_without_server_error(settings) -> None:
    class FailingModel:
        async def turn(self, messages, tools):
            raise OSError("network unavailable")

    workflow = ResearchWorkflow(
        settings,
        search=PaperSearchService([]),
        writer=ResearchWriter(FailingModel()),
    )

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/search",
            json={"query": "珊瑚白化与海水温度变化", "natural_language": True},
        )

    assert response.status_code == 422
    assert "network unavailable" in response.json()["detail"]


def test_search_api_normalizes_unicode_url_and_rejects_control_characters(settings) -> None:
    source = MemorySource(
        [
            Paper(
                title="URL normalization",
                authors=[],
                year=2026,
                abstract="",
                sources=["memory"],
                external_id="normalized-url",
                landing_url="https://example.org/论文 data?q=引用 可靠",
                pdf_url="https://example.org/\x1b]8;;https://evil.example\x07",
            )
        ]
    )
    workflow = ResearchWorkflow(settings, search=PaperSearchService([source]))

    with TestClient(create_app(workflow)) as client:
        response = client.post(
            "/api/search",
            json={"query": "normalization", "sources": ["memory"], "limit": 1},
        )

    paper = response.json()["papers"][0]
    assert paper["landing_url"] == (
        "https://example.org/%E8%AE%BA%E6%96%87%20data?"
        "q=%E5%BC%95%E7%94%A8%20%E5%8F%AF%E9%9D%A0"
    )
    assert paper["pdf_url"] is None


def test_duplicate_run_is_rejected(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    with TestClient(create_app(workflow)) as client:
        created = client.post(
            "/api/projects",
            json={"idea": "设计一个可追踪的科研智能体评估方案", "run_now": True},
        )
        project_id = created.json()["id"]
        duplicate = client.post(f"/api/projects/{project_id}/run")
        assert duplicate.status_code in {202, 409}
