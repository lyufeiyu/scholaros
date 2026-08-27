from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest

from scholaros.domain import Evidence, Paper, ProjectStatus, ResearchSpec, SearchField, Stage
from scholaros.papers import MemorySource, PaperSearchService
from scholaros.runtime import ModelTurn
from scholaros.storage import ProjectBusyError, ProjectStore
from scholaros.workflow import ResearchWorkflow
from scholaros.writing import ResearchWriter, _evidence_context


def test_offline_scope_builds_english_search_keywords() -> None:
    spec = ResearchWriter._fallback_spec("多智能体科研助手的引用可靠性与研究设计质量")
    assert "multi-agent systems" in spec.keywords
    assert "citation reliability" in spec.keywords


@pytest.mark.asyncio
async def test_scope_uses_uploaded_source_as_verifiable_domain_anchor() -> None:
    class RecordingModel:
        def __init__(self):
            self.prompt = ""

        async def turn(self, messages, tools):
            del tools
            self.prompt = messages[-1].content
            return ModelTurn(
                """{
                    "title": "LLM-guided localized feature selection",
                    "question": "How can LLM priors improve localized feature selection?",
                    "contribution": "A grounded hybrid feature-selection framework.",
                    "hypotheses": ["Semantic priors improve local feature stability."],
                    "keywords": ["localized feature selection", "evolutionary multitask localized feature selection", "LLM-guided localized feature selection"],
                    "paper_type": "empirical",
                    "target_audience": "feature-selection researchers",
                    "source_basis": "Localized feature selection (LFS) partitions the sample space"
                }"""
            )

    model = RecordingModel()
    writer = ResearchWriter(model)
    documents = [
        {
            "name": "EMaTO-LFS.pdf",
            "excerpt": (
                "Localized feature selection (LFS) partitions the sample space into multiple "
                "local regions. Evolutionary many-task optimization shares knowledge among them."
            ),
        }
    ]

    spec = await writer.scope("学习上传论文并研究如何结合 LLM", documents)

    assert "EMaTO-LFS.pdf" in model.prompt
    assert "Git Large File Storage" not in spec.title
    assert spec.source_basis in documents[0]["excerpt"]


@pytest.mark.asyncio
async def test_scope_rejects_unverifiable_source_grounding() -> None:
    class DriftingModel:
        async def turn(self, messages, tools):
            del messages, tools
            return ModelTurn(
                """{
                    "title": "Git Large File Storage management",
                    "question": "How can LLMs manage Git LFS assets?",
                    "contribution": "Semantic Git asset retrieval.",
                    "hypotheses": ["LLMs improve binary asset retrieval."],
                    "keywords": ["Git Large File Storage", "semantic retrieval", "binary assets"],
                    "paper_type": "empirical",
                    "target_audience": "software engineers",
                    "source_basis": "Localized feature selection partitions samples"
                }"""
            )

    writer = ResearchWriter(DriftingModel())
    documents = [
        {
            "name": "EMaTO-LFS.pdf",
            "role": "source",
            "excerpt": "Localized feature selection partitions samples into local regions.",
        }
    ]

    with pytest.raises(RuntimeError, match="范围界定|主题不一致"):
        await writer.scope("学习上传论文并结合 LLM", documents)


@pytest.mark.asyncio
async def test_scope_does_not_accept_filename_or_cross_document_text_as_grounding() -> None:
    class FilenameModel:
        async def turn(self, messages, tools):
            del messages, tools
            return ModelTurn(
                """{
                    "title": "EMaTO-LFS feature selection",
                    "question": "How should EMaTO-LFS select features?",
                    "contribution": "A grounded feature selection method.",
                    "hypotheses": ["H1"],
                    "keywords": ["EMaTO LFS feature selection", "EMaTO LFS local selection", "EMaTO LFS classification"],
                    "paper_type": "empirical",
                    "target_audience": "feature selection researchers",
                    "source_basis": "EMaTO LFS source paper"
                }"""
            )

    documents = [
        {
            "name": "EMaTO LFS source paper.pdf",
            "role": "source",
            "excerpt": "Localized feature selection partitions samples into local regions.",
        }
    ]
    with pytest.raises(RuntimeError, match="范围界定|主题不一致"):
        await ResearchWriter(FilenameModel()).scope("学习上传论文中的 LFS", documents)


@pytest.mark.asyncio
async def test_scope_rejects_structurally_unsafe_external_search_keyword() -> None:
    class UnsafeKeywordModel:
        async def turn(self, messages, tools):
            del messages, tools
            return ModelTurn(
                """{
                    "title": "LLM-guided localized feature selection",
                    "question": "How can LLMs improve localized feature selection?",
                    "contribution": "A grounded hybrid method.",
                    "hypotheses": ["H1"],
                    "keywords": ["localized feature selection", "localized feature selection https://private.example", "LLM localized feature selection"],
                    "paper_type": "empirical",
                    "target_audience": "feature selection researchers",
                    "source_basis": "Localized feature selection partitions samples"
                }"""
            )

    documents = [{
        "name": "paper.pdf",
        "role": "source",
        "excerpt": "Localized feature selection partitions samples into local regions.",
    }]
    with pytest.raises(RuntimeError, match="可核验的范围界定") as error:
        await ResearchWriter(UnsafeKeywordModel()).scope("学习论文并结合 LLM", documents)
    assert "private.example" not in str(error.value)


@pytest.mark.asyncio
async def test_scope_preserves_actionable_model_transport_error_with_source() -> None:
    class TimeoutModel:
        async def turn(self, messages, tools):
            del messages, tools
            raise RuntimeError(
                "模型读取响应超过 300 秒；请调整 SCHOLAROS_MODEL_TIMEOUT_SECONDS"
            )

    documents = [{
        "name": "paper.pdf",
        "role": "source",
        "excerpt": "Localized feature selection partitions samples into local regions.",
    }]
    with pytest.raises(RuntimeError, match="SCHOLAROS_MODEL_TIMEOUT_SECONDS"):
        await ResearchWriter(TimeoutModel()).scope("学习上传论文中的 LFS", documents)


async def test_prepare_search_query_preserves_direct_or_interprets_natural_language(
    settings,
) -> None:
    class KeywordWriter(ResearchWriter):
        async def search_keywords(self, query: str) -> list[str]:
            assert query == "多智能体科研助手的引用可靠性"
            return ["multi-agent systems", "citation reliability"]

    workflow = ResearchWorkflow(
        settings, search=PaperSearchService([]), writer=KeywordWriter()
    )

    assert await workflow.prepare_search_query("FMM-Agent") == "FMM-Agent"
    interpreted = await workflow.prepare_search_query(
        "多智能体科研助手的引用可靠性", natural_language=True
    )

    assert "multi-agent systems" in interpreted
    assert "citation reliability" in interpreted

    plan = await workflow.prepare_search_plan(
        "多智能体科研助手的引用可靠性", natural_language=True
    )
    assert plan.input_query == "多智能体科研助手的引用可靠性"
    assert plan.search_terms == ["multi-agent systems", "citation reliability"]
    assert plan.search_query == "multi-agent systems citation reliability"
    assert plan.field == SearchField.ALL


async def test_natural_language_is_rejected_for_structured_search_field(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))

    with pytest.raises(ValueError, match="仅用于综合主题"):
        await workflow.prepare_search_plan(
            "Geoffrey Hinton", natural_language=True, field=SearchField.AUTHOR
        )


async def test_natural_language_search_requires_a_working_model(settings) -> None:
    workflow = ResearchWorkflow(
        settings, search=PaperSearchService([]), writer=ResearchWriter()
    )

    with pytest.raises(ValueError, match="未配置可用模型"):
        await workflow.prepare_search_query(
            "珊瑚白化与海水温度变化", natural_language=True
        )


def test_document_role_is_validated(settings, tmp_path) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("评估科研智能体的引用可靠性与研究设计质量")
    path = tmp_path / "results.md"
    path.write_text("实验结果由用户提供。", encoding="utf-8")
    item = workflow.add_document(project.id, path, role="results")
    assert item["role"] == "results"
    with pytest.raises(ValueError, match="source 或 results"):
        workflow.add_document(project.id, path, role="unknown")


def test_ieee_is_search_only_and_rejected_from_ai_workflow(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))

    with pytest.raises(ValueError, match="仅支持独立检索"):
        workflow.create_project("验证 IEEE 内容不会未经授权进入模型写作链路", ["ieee"])


async def test_legacy_ieee_project_is_rejected_again_when_run(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证升级前 IEEE 项目也不能绕过模型写作门禁")
    project.selected_sources = ["ieee"]
    workflow.store.save_project(project)

    with pytest.raises(ValueError, match="仅支持独立检索"):
        await workflow.run(project.id)

    assert workflow.store.get_project(project.id).status == ProjectStatus.CREATED


def test_document_upload_is_rejected_while_running(settings, tmp_path) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("评估科研智能体的引用可靠性与研究设计质量")
    project.status = ProjectStatus.RUNNING
    workflow.store.save_project(project)
    path = tmp_path / "results.md"
    path.write_text("真实结果", encoding="utf-8")

    with pytest.raises(RuntimeError, match="运行中不能上传"):
        workflow.add_document(project.id, path, role="results")


def test_results_document_has_context_priority() -> None:
    evidence = [
        Evidence(f"E{index}", "论文", "长摘要" * 500, [], [], None) for index in range(30)
    ]
    documents = [
        {
            "name": "results.md",
            "role": "results",
            "text": "UNIQUE_REAL_RESULT",
        }
    ]

    context = _evidence_context(evidence, documents, max_chars=1000)

    assert "UNIQUE_REAL_RESULT" in context


async def test_offline_design_is_topic_generic() -> None:
    writer = ResearchWriter()
    spec = writer._fallback_spec("珊瑚白化与海水温度变化的关系")

    design = await writer.design(spec, [])

    assert spec.question == design["research_question"]
    assert "ScholarOS" not in str(design)
    assert "通用对话式 LLM" not in str(design)


def test_artifact_path_rejects_traversal(settings) -> None:
    store = ProjectStore(settings)
    assert store.artifact_path("..", "scholaros.db") is None
    with pytest.raises(ValueError, match="项目 ID"):
        store.save_artifact("..", "escape.txt", "no")


def test_delete_retries_when_artifact_cleanup_fails(settings, monkeypatch) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证制品清理失败时不会只删除数据库项目记录")
    artifact = workflow.store.save_artifact(project.id, "paper.md", "# keep")

    original_cleanup = workflow.store._remove_tree

    def fail_cleanup(_path) -> None:
        raise OSError("disk error")

    monkeypatch.setattr(workflow.store, "_remove_tree", fail_cleanup)
    with pytest.raises(OSError, match="disk error"):
        workflow.store.delete_project(project.id)

    assert workflow.store.get_project(project.id) is None
    assert workflow.store.list_events(project.id) == []
    assert not artifact.parent.exists()
    assert list(settings.artifacts_path.glob(f".deleting-{project.id}-*"))

    monkeypatch.setattr(workflow.store, "_remove_tree", original_cleanup)
    assert workflow.store.delete_project(project.id) is True
    assert not list(settings.artifacts_path.glob(f".deleting-{project.id}-*"))


async def test_offline_workflow_generates_complete_paper(settings) -> None:
    source = MemorySource(
        [
            Paper(
                title="Evidence-grounded research agents for citation reliability",
                authors=["Lin Chen"],
                year=2025,
                abstract=(
                    "A method for evidence-grounded scientific workflows and "
                    "research design quality."
                ),
                sources=["memory"],
                external_id="paper-1",
                doi="10.1/grounded",
            )
        ]
    )
    workflow = ResearchWorkflow(settings, search=PaperSearchService([source]))
    project = workflow.create_project("评估科研智能体的引用可靠性与研究设计质量")

    result = await workflow.run(project.id)

    assert result.status == ProjectStatus.COMPLETED
    assert result.stage == Stage.COMPLETED
    assert result.state["final_review"]["passed"] is True
    assert result.state["final_review"]["metrics"]["quality_checks"] == 9
    assert result.state["final_review"]["metrics"]["quality_checks_passed"] == 9
    paper_path = workflow.store.artifact_path(project.id, "paper.md")
    assert paper_path is not None
    paper = paper_path.read_text(encoding="utf-8")
    assert "图 1 设计说明" in paper
    assert "表 2 设计说明" in paper
    assert "当前没有真实实验数据" in paper
    assert "研究者责任与 AI 辅助说明" in paper
    assert "承担最终责任" in paper
    assert f"[@{result.state['papers'][0]['cite_key']}]" in paper
    events = workflow.store.list_events(project.id)
    assert len(events) == 16
    assert events[-1]["type"] == "workflow_completed"

    reloaded = ProjectStore(settings).get_project(project.id)
    assert reloaded is not None
    assert reloaded.status == ProjectStatus.COMPLETED


@pytest.mark.asyncio
async def test_workflow_passes_uploaded_documents_to_scope(settings, tmp_path) -> None:
    class DocumentAwareWriter(ResearchWriter):
        def __init__(self):
            super().__init__()
            self.documents = []

        async def scope(self, idea: str, documents=()):
            del idea
            self.documents = list(documents)
            return ResearchSpec(
                title="LLM-guided localized feature selection",
                question="How can LLM priors improve localized feature selection?",
                contribution="Grounded local feature priors.",
                hypotheses=["H1"],
                keywords=["localized feature selection"],
                source_basis="Localized feature selection",
            )

    writer = DocumentAwareWriter()
    source = MemorySource(
        [
            Paper(
                title="Localized feature selection for classification",
                authors=["Ada Smith"],
                year=2025,
                abstract="Localized feature selection adapts features to local regions.",
                sources=["memory"],
                external_id="lfs-paper",
            )
        ]
    )
    workflow = ResearchWorkflow(
        settings, search=PaperSearchService([source]), writer=writer
    )
    project = workflow.create_project("学习上传论文并研究 LLM 与局部特征选择的结合")
    document_path = tmp_path / "EMaTO-LFS.txt"
    document_path.write_text(
        "Localized feature selection partitions samples into local regions.",
        encoding="utf-8",
    )
    workflow.add_document(project.id, document_path)

    await workflow.run(project.id)

    assert writer.documents[0]["name"] == "EMaTO-LFS.txt"


@pytest.mark.asyncio
async def test_workflow_stops_before_drafting_when_search_is_empty(settings) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("研究局部特征选择与大语言模型语义先验的结合")

    with pytest.raises(RuntimeError, match="没有检索到与研究主题匹配的论文"):
        await workflow.run(project.id)

    saved = workflow.store.get_project(project.id)
    assert saved is not None
    assert saved.status == ProjectStatus.FAILED
    assert saved.stage == Stage.SEARCHING
    assert saved.state["papers"] == []
    assert saved.state["search_queries"]
    assert workflow.store.artifact_path(project.id, "paper-draft.md") is None


@pytest.mark.asyncio
async def test_explicit_offline_workflow_can_generate_without_search_results(settings) -> None:
    workflow = ResearchWorkflow(
        settings,
        search=PaperSearchService([]),
        allow_empty_search=True,
    )
    project = workflow.create_project("离线演示科研智能体的引用可靠性评估流程")

    result = await workflow.run(project.id)

    assert result.stage == Stage.COMPLETED
    assert result.state["papers"] == []
    assert workflow.store.artifact_path(project.id, "paper.md") is not None


@pytest.mark.asyncio
async def test_restart_removes_stale_generated_artifacts_but_preserves_sources(
    settings, tmp_path
) -> None:
    source = MemorySource(
        [
            Paper(
                title="Research agents for citation reliability",
                authors=["Ada Smith"],
                year=2025,
                abstract="Multi-agent systems improve citation reliability and research agents.",
                sources=["memory"],
                external_id="restart-paper",
            )
        ]
    )
    workflow = ResearchWorkflow(settings, search=PaperSearchService([source]))
    project = workflow.create_project("研究科研智能体的引用可靠性与研究设计质量")
    source_path = tmp_path / "notes.txt"
    source_path.write_text("Trusted research notes.", encoding="utf-8")
    document = workflow.add_document(project.id, source_path)
    pending = await workflow.run(project.id)
    assert pending.state["search_confirmation_required"] is True
    workflow.confirm_search_plan(project.id)
    await workflow.run(project.id)
    assert workflow.store.artifact_path(project.id, "paper.md") is not None

    workflow.search = PaperSearchService([])
    pending = await workflow.run(project.id, restart=True)
    assert pending.state["search_confirmation_required"] is True
    assert workflow.store.artifact_path(project.id, "paper.md") is None
    workflow.confirm_search_plan(project.id)
    with pytest.raises(RuntimeError, match="没有检索到"):
        await workflow.run(project.id)

    assert workflow.store.artifact_path(project.id, document["artifact"]) is not None
    assert workflow.store.artifact_path(project.id, "paper.md") is None
    assert workflow.store.artifact_path(project.id, "paper-draft.md") is None
    assert workflow.store.artifact_path(project.id, "evidence.json") is None
    papers_path = workflow.store.artifact_path(project.id, "papers.json")
    assert papers_path is not None
    assert papers_path.read_text(encoding="utf-8").strip() == "[]"


@pytest.mark.asyncio
async def test_uploaded_source_search_plan_is_confirmed_before_external_requests(
    settings, tmp_path
) -> None:
    class SourceAwareWriter(ResearchWriter):
        async def scope(self, idea: str, documents=()):
            del idea, documents
            return ResearchSpec(
                title="LLM-guided localized feature selection",
                question="How can LLMs improve localized feature selection?",
                contribution="A grounded hybrid method.",
                hypotheses=["H1"],
                keywords=[
                    "localized feature selection",
                    "evolutionary optimization",
                    "LLM feature selection",
                ],
                source_basis="Localized feature selection partitions samples",
            )

    class CountingSource(MemorySource):
        def __init__(self, papers):
            super().__init__(papers)
            self.calls = 0

        async def search(self, query, limit, field=SearchField.ALL):
            self.calls += 1
            return await super().search(query, limit, field)

    source = CountingSource(
        [
            Paper(
                title="Localized feature selection",
                authors=["Ada Smith"],
                year=2025,
                abstract="Localized feature selection partitions samples.",
                sources=["memory"],
                external_id="core",
            ),
            Paper(
                title="Evolutionary optimization",
                authors=["Bo Chen"],
                year=2025,
                abstract="Evolutionary optimization for classification.",
                sources=["memory"],
                external_id="method",
            ),
        ]
    )
    workflow = ResearchWorkflow(
        settings, search=PaperSearchService([source]), writer=SourceAwareWriter()
    )
    project = workflow.create_project("学习上传的 LFS 论文并研究如何结合 LLM")
    path = tmp_path / "paper.txt"
    path.write_text(
        "Localized feature selection partitions samples into local regions.",
        encoding="utf-8",
    )
    workflow.add_document(project.id, path)

    pending = await workflow.run(project.id)

    assert pending.status == ProjectStatus.NEEDS_ATTENTION
    assert pending.state["search_confirmation_required"] is True
    assert pending.state["search_queries"] == [
        "localized feature selection",
        "evolutionary optimization",
        "LLM feature selection",
    ]
    assert source.calls == 0

    workflow.confirm_search_plan(project.id)
    completed = await workflow.run(project.id)

    assert source.calls == 3
    assert {paper["external_id"] for paper in completed.state["papers"]} == {
        "core",
        "method",
    }


@pytest.mark.asyncio
async def test_rejecting_search_plan_allows_revised_idea_without_external_request(
    settings, tmp_path
) -> None:
    class SourceAwareWriter(ResearchWriter):
        async def scope(self, idea: str, documents=()):
            del idea, documents
            return ResearchSpec(
                title="LLM-guided localized feature selection",
                question="How can LLMs improve localized feature selection?",
                contribution="A grounded method.",
                hypotheses=["H1"],
                keywords=[
                    "localized feature selection",
                    "evolutionary optimization",
                    "LLM feature selection",
                ],
                source_basis="Localized feature selection partitions samples",
            )

    class CountingSource(MemorySource):
        def __init__(self):
            super().__init__([])
            self.calls = 0

        async def search(self, query, limit, field=SearchField.ALL):
            del query, limit, field
            self.calls += 1
            return []

    source = CountingSource()
    workflow = ResearchWorkflow(
        settings, search=PaperSearchService([source]), writer=SourceAwareWriter()
    )
    project = workflow.create_project("学习上传论文中的 LFS 并结合 LLM")
    path = tmp_path / "paper.txt"
    path.write_text("Localized feature selection partitions samples.", encoding="utf-8")
    workflow.add_document(project.id, path)
    await workflow.run(project.id)

    revised = "明确研究 Localized Feature Selection 与 LLM 语义先验的结合"
    reset = workflow.reject_search_plan(project.id, revised_idea=revised)

    assert reset.status == ProjectStatus.CREATED
    assert reset.stage == Stage.SCOPING
    assert reset.idea == revised
    assert reset.state.get("search_queries") is None
    assert source.calls == 0

    pending_again = await workflow.run(project.id)
    assert pending_again.state["search_confirmation_required"] is True
    assert source.calls == 0


@pytest.mark.asyncio
async def test_busy_confirmation_keeps_pending_plan_recoverable(settings, monkeypatch) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证确认检索计划遇到并发锁时不会半提交状态")
    project.stage = Stage.SEARCHING
    project.status = ProjectStatus.NEEDS_ATTENTION
    project.state.update(
        {
            "search_confirmation_required": True,
            "search_queries": ["localized feature selection"],
        }
    )
    workflow.store.save_project(project)

    @contextmanager
    def busy_lock(_project_id):
        raise ProjectBusyError("项目正在另一个 ScholarOS 进程中运行")
        yield

    monkeypatch.setattr(workflow.store, "project_lock", busy_lock)
    with pytest.raises(ProjectBusyError):
        await workflow.confirm_search_plan_and_run(project.id)

    saved = workflow.store.get_project(project.id)
    assert saved is not None
    assert saved.state["search_confirmation_required"] is True
    assert saved.state.get("search_plan_confirmed") is not True


def test_reclassifying_source_as_results_invalidates_pending_plan(settings, tmp_path) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证资料角色变化会使旧检索计划失效")
    path = tmp_path / "paper.txt"
    path.write_text("Localized feature selection source.", encoding="utf-8")
    workflow.add_document(project.id, path, role="source")
    project = workflow.store.get_project(project.id)
    assert project is not None
    project.stage = Stage.SEARCHING
    project.status = ProjectStatus.NEEDS_ATTENTION
    project.state.update(
        {
            "spec": {"title": "old"},
            "search_confirmation_required": True,
            "search_queries": ["old source query"],
        }
    )
    workflow.store.save_project(project)

    updated = workflow.add_document(project.id, path, role="results")

    saved = workflow.store.get_project(project.id)
    assert saved is not None
    assert updated["role"] == "results"
    assert saved.status == ProjectStatus.CREATED
    assert saved.stage == Stage.SCOPING
    assert saved.state.get("spec") is None
    assert saved.state.get("search_confirmation_required") is None
    with pytest.raises(ValueError, match="没有待确认"):
        workflow.confirm_search_plan(project.id)


@pytest.mark.asyncio
async def test_legitimate_secret_sharing_topic_reaches_confirmation_without_external_request(
    settings, tmp_path
) -> None:
    class SecretSharingWriter(ResearchWriter):
        async def scope(self, idea: str, documents=()):
            del idea, documents
            return ResearchSpec(
                title="LLM-assisted secret sharing",
                question="How can LLMs improve secret sharing?",
                contribution="A secure protocol design.",
                hypotheses=["H1"],
                keywords=["secret sharing", "password security", "LLM secret sharing"],
                source_basis="Secret sharing distributes cryptographic trust",
            )

    class CountingSource(MemorySource):
        def __init__(self):
            super().__init__([])
            self.calls = 0

        async def search(self, query, limit, field=SearchField.ALL):
            del query, limit, field
            self.calls += 1
            return []

    source = CountingSource()
    workflow = ResearchWorkflow(
        settings, search=PaperSearchService([source]), writer=SecretSharingWriter()
    )
    project = workflow.create_project("研究上传论文中的 secret sharing 与 LLM")
    path = tmp_path / "crypto.txt"
    path.write_text(
        "Secret sharing distributes cryptographic trust. Password security is evaluated.",
        encoding="utf-8",
    )
    workflow.add_document(project.id, path)

    pending = await workflow.run(project.id)

    assert pending.state["search_confirmation_required"] is True
    assert pending.state["search_plan_warnings"]
    assert source.calls == 0


@pytest.mark.asyncio
async def test_chinese_source_with_english_search_terms_reaches_human_confirmation(
    settings, tmp_path
) -> None:
    class ChineseSourceModel:
        async def turn(self, messages, tools):
            del messages, tools
            return ModelTurn(
                """{
                    "title": "LLM-guided localized feature selection",
                    "question": "How can LLMs improve localized feature selection?",
                    "contribution": "A grounded hybrid method.",
                    "hypotheses": ["H1"],
                    "keywords": ["localized feature selection", "evolutionary optimization", "LLM feature selection"],
                    "paper_type": "empirical",
                    "target_audience": "feature selection researchers",
                    "source_basis": "局部特征选择将样本空间划分为多个区域"
                }"""
            )

    class CountingSource(MemorySource):
        def __init__(self):
            super().__init__([])
            self.calls = 0

        async def search(self, query, limit, field=SearchField.ALL):
            del query, limit, field
            self.calls += 1
            return []

    source = CountingSource()
    workflow = ResearchWorkflow(
        settings,
        search=PaperSearchService([source]),
        writer=ResearchWriter(ChineseSourceModel()),
    )
    project = workflow.create_project("学习中文资料中的局部特征选择并结合 LLM")
    path = tmp_path / "paper.txt"
    path.write_text(
        "局部特征选择将样本空间划分为多个区域，并为每个区域选择特征子集。",
        encoding="utf-8",
    )
    workflow.add_document(project.id, path)

    pending = await workflow.run(project.id)

    assert pending.state["search_confirmation_required"] is True
    assert any("跨语言" in item for item in pending.state["search_plan_warnings"])
    assert source.calls == 0


@pytest.mark.asyncio
async def test_restart_cleanup_failure_is_persisted_as_failed(settings, monkeypatch) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证重新运行清理失败不会保留已完成状态")
    project.status = ProjectStatus.COMPLETED
    project.stage = Stage.COMPLETED
    workflow.store.save_project(project)
    workflow.store.save_artifact(project.id, "paper.md", "# stale")
    original_cleanup = workflow.store.clear_generated_artifacts
    calls = 0

    def fail_cleanup(_project_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("cleanup failed")
        original_cleanup(_project_id)

    monkeypatch.setattr(workflow.store, "clear_generated_artifacts", fail_cleanup)
    with pytest.raises(OSError, match="cleanup failed"):
        await workflow.run(project.id, restart=True)

    saved = workflow.store.get_project(project.id)
    assert saved is not None
    assert saved.status == ProjectStatus.FAILED
    assert saved.stage == Stage.SCOPING
    assert "cleanup failed" in (saved.error or "")
    assert saved.state["clear_generated_on_next_run"] is True

    with pytest.raises(RuntimeError, match="没有检索到"):
        await workflow.run(project.id)
    retried = workflow.store.get_project(project.id)
    assert retried is not None
    assert calls == 2
    assert "clear_generated_on_next_run" not in retried.state
    assert workflow.store.artifact_path(project.id, "paper.md") is None


@pytest.mark.asyncio
async def test_workflow_keeps_legacy_one_argument_scope_writer_compatible(settings) -> None:
    class LegacyWriter(ResearchWriter):
        async def scope(self, idea: str):
            del idea
            return ResearchSpec(
                title="Citation reliability",
                question="How can research agents improve citation reliability?",
                contribution="A testable workflow.",
                hypotheses=["H1"],
                keywords=["citation reliability"],
            )

    source = MemorySource(
        [
            Paper(
                title="Citation reliability for research agents",
                authors=["Ada Smith"],
                year=2025,
                abstract="Research agents improve citation reliability.",
                sources=["memory"],
                external_id="legacy-writer-paper",
            )
        ]
    )
    workflow = ResearchWorkflow(
        settings, search=PaperSearchService([source]), writer=LegacyWriter()
    )
    project = workflow.create_project("验证旧版自定义写作器仍然兼容当前工作流")

    result = await workflow.run(project.id)

    assert result.status == ProjectStatus.COMPLETED


@pytest.mark.asyncio
async def test_scope_adapter_supports_keyword_only_and_kwargs_documents(settings) -> None:
    received = []

    class KeywordOnlyWriter(ResearchWriter):
        async def scope(self, idea: str, *, documents=()):
            del idea
            received.append(list(documents))

    class KwargsWriter(ResearchWriter):
        async def scope(self, idea: str, **kwargs):
            del idea
            received.append(list(kwargs["documents"]))

    for writer in (KeywordOnlyWriter(), KwargsWriter()):
        workflow = ResearchWorkflow(
            settings, search=PaperSearchService([]), writer=writer
        )
        project = workflow.create_project("验证自定义写作器的资料参数签名兼容性")
        project.state["documents"] = [{"id": "source"}]
        await workflow._scope(project)

    assert received == [[{"id": "source"}], [{"id": "source"}]]


async def test_workflow_emits_progress_to_terminal_sink(settings) -> None:
    events = []
    source = MemorySource(
        [
                Paper(
                    title="Research agents for citation reliability",
                    authors=["Lin Chen"],
                    year=2025,
                    abstract="Research agents improve citation reliability and research design quality.",
                sources=["memory"],
                external_id="progress-paper",
            )
        ]
    )
    workflow = ResearchWorkflow(
        settings,
        search=PaperSearchService([source]),
        progress_sink=lambda event_type, payload: events.append((event_type, payload)),
    )
    project = workflow.create_project("验证科研智能体的引用可靠性与研究设计质量")

    await workflow.run(project.id)

    started = [payload["stage"] for event, payload in events if event == "stage_started"]
    assert started == [stage.value for stage in workflow.stage_order]


async def test_cancelled_workflow_is_not_left_running(settings) -> None:
    class SlowWriter(ResearchWriter):
        async def scope(self, idea: str, documents=()):
            del idea, documents
            await asyncio.sleep(60)

    workflow = ResearchWorkflow(
        settings,
        search=PaperSearchService([]),
        writer=SlowWriter(),
    )
    project = workflow.create_project("验证服务停止后项目不会永久停留在运行中")
    task = asyncio.create_task(workflow.run(project.id))
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    saved = workflow.store.get_project(project.id)
    assert saved is not None
    assert saved.status == ProjectStatus.FAILED
    assert "中断" in (saved.error or "")
    assert workflow.store.list_events(project.id)[-1]["type"] == "workflow_cancelled"
