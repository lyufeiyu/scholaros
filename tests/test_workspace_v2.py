from __future__ import annotations

import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from scholaros.api import create_app
from scholaros.delivery import markdown_to_docx, markdown_to_tex
from scholaros.domain import Stage
from scholaros.ingestion import DocumentIngestor
from scholaros.papers import MemorySource, PaperSearchService
from scholaros.workflow import ResearchWorkflow
from scholaros.workspace import normalize_configuration
from scholaros.writing import ResearchWriter


def offline_flow(settings) -> ResearchWorkflow:
    return ResearchWorkflow(
        settings,
        search=PaperSearchService([]),
        allow_empty_search=True,
    )


def test_configuration_is_complete_and_strict() -> None:
    config = normalize_configuration({"workflow": "transfer", "formats": ["tex", "md", "md"]})
    assert config["workflow"] == "transfer"
    assert config["formats"] == ["tex", "md"]
    assert config["same_field_papers"] == 3
    with pytest.raises(ValueError, match="未知项目配置"):
        normalize_configuration({"workflow": "review", "secret_mode": True})
    with pytest.raises(ValueError, match="reference_count"):
        normalize_configuration({"reference_count_mode": "custom"})


def test_docx_and_tex_exports_are_editable_and_ingestible(tmp_path) -> None:
    markdown = "# 研究标题\n\n正文包含 [@Source1] 与 **重点**。"
    docx_path = tmp_path / "paper.docx"
    docx_path.write_bytes(markdown_to_docx(markdown))
    with zipfile.ZipFile(docx_path) as archive:
        assert "word/document.xml" in archive.namelist()
    document = DocumentIngestor().ingest(docx_path)
    assert "研究标题" in document.text
    assert "重点" in document.text
    tex = markdown_to_tex(markdown, language="zh")
    assert "\\documentclass[11pt]{ctexart}" in tex
    assert "\\cite{Source1}" in tex


async def test_guided_contribution_choice_updates_same_project(settings) -> None:
    flow = offline_flow(settings)
    project = flow.create_project("验证贡献候选在同一个研究项目中保存", guided=True)
    pending = await flow.run(project.id)
    assert pending.state["pending_checkpoint"] == "scoping"
    assert len(pending.state["contribution_options"]) == 3
    updated = flow.save_decision(
        project.id,
        decision_type="contribution",
        item_id="validation",
        value="selected",
        comment="优先说明失败边界",
    )
    assert updated.id == project.id
    assert updated.state["selected_contribution"] == "validation"
    assert "失败边界" in updated.state["spec"]["contribution"]
    assert updated.state["decisions"][0]["comment"] == "优先说明失败边界"


async def test_figure_feedback_and_delivery_lifecycle(settings) -> None:
    flow = offline_flow(settings)
    project = flow.create_project(
        "验证图件、反馈与交付中心共享同一份项目状态",
        configuration={"formats": ["md", "docx", "tex", "pdf"]},
    )
    completed = await flow.run(project.id)
    assert completed.stage == Stage.COMPLETED
    assert len(completed.state["figure_story"]) == 3
    flow.save_decision(
        project.id,
        decision_type="figure",
        item_id="figure-1",
        value="revise",
        comment="后续补机制图素材",
    )
    figure_story = json.loads(
        flow.store.artifact_path(project.id, "figure-story.json").read_text(encoding="utf-8")
    )
    assert figure_story[0]["decision"] == "revise"
    assert figure_story[0]["comment"] == "后续补机制图素材"
    delivered = flow.prepare_delivery(project.id)
    manifest = delivered.state["delivery_manifest"]
    assert manifest["available_formats"] == ["md", "docx", "tex"]
    assert manifest["missing_formats"] == ["pdf"]
    assert manifest["ready"] is False
    package = flow.store.artifact_path(project.id, "delivery-package.zip")
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
    assert {"paper.md", "paper.docx", "paper.tex", "delivery-manifest.json"} <= names
    assert not any(name.startswith("source-") for name in names)

    changed = flow.save_decision(
        project.id,
        decision_type="figure",
        item_id="figure-2",
        value="omit",
        comment="没有真实结果前不进入交付包",
    )
    assert "delivery_manifest" not in changed.state
    assert flow.store.artifact_path(project.id, "delivery-package.zip") is None
    assert flow.store.artifact_path(project.id, "paper.docx") is None
    assert flow.store.list_history(project.id)

    flow.prepare_delivery(project.id)
    feedback = flow.add_feedback(project.id, scope="manuscript", text="把局限部分写得更具体。")
    assert feedback.stage == Stage.REVISING
    assert feedback.state["feedback"][-1]["status"] == "pending"
    assert flow.store.artifact_path(project.id, "delivery-package.zip") is None
    resumed = await flow.resume(project.id)
    assert resumed.state["feedback"][-1]["status"] == "manual_required"
    assert flow.store.list_history(project.id)


async def test_repeated_feedback_revises_the_latest_completed_paper(settings) -> None:
    class FeedbackWriter(ResearchWriter):
        async def revise(self, paper, findings, feedback=()):
            additions = "\n".join(item["text"] for item in feedback)
            return f"{paper}\n{additions}" if additions else paper

    flow = ResearchWorkflow(
        settings,
        search=PaperSearchService([]),
        writer=FeedbackWriter(),
        allow_empty_search=True,
    )
    project = flow.create_project("验证连续两轮返修都以前一轮完成稿为基础")
    await flow.run(project.id)

    flow.add_feedback(project.id, scope="manuscript", text="第一轮新增限制说明")
    first = await flow.resume(project.id)
    first_paper = flow.store.artifact_path(first.id, "paper.md").read_text(encoding="utf-8")
    assert "第一轮新增限制说明" in first_paper

    flow.add_feedback(project.id, scope="manuscript", text="第二轮补充外部效度")
    second = await flow.resume(project.id)
    second_paper = flow.store.artifact_path(second.id, "paper.md").read_text(encoding="utf-8")
    assert "第一轮新增限制说明" in second_paper
    assert "第二轮补充外部效度" in second_paper


async def test_figure_feedback_updates_storyboard_instead_of_claiming_manuscript_edit(
    settings,
) -> None:
    flow = offline_flow(settings)
    project = flow.create_project("验证图件反馈被写入故事板并独立记录处理状态")
    await flow.run(project.id)
    paper_before = flow.store.artifact_path(project.id, "paper.md").read_bytes()

    flow.add_feedback(project.id, scope="figures", text="所有图统一标记证据层级")
    revised = await flow.resume(project.id)

    feedback = revised.state["feedback"][-1]
    assert feedback["status"] == "applied"
    assert feedback["components"]["figures"] == "applied"
    story = json.loads(
        flow.store.artifact_path(project.id, "figure-story.json").read_text(encoding="utf-8")
    )
    assert story[0]["revision_requested"] is True
    assert story[0]["feedback_requests"][-1]["text"] == "所有图统一标记证据层级"
    assert flow.store.artifact_path(project.id, "paper.md").read_bytes() == paper_before


async def test_materials_only_never_calls_external_search(settings, tmp_path) -> None:
    class ForbiddenSource(MemorySource):
        async def search(self, *args, **kwargs):
            raise AssertionError("materials_only 不得调用外部论文源")

    flow = ResearchWorkflow(
        settings,
        search=PaperSearchService([ForbiddenSource([])]),
    )
    project = flow.create_project(
        "验证只使用已有材料模式不会访问任何第三方论文源",
        configuration={"research_mode": "materials_only"},
    )
    source = tmp_path / "local-notes.md"
    source.write_text("本地研究材料包含可核验的流程与约束。", encoding="utf-8")
    flow.add_document(project.id, source)

    completed = await flow.run(project.id)

    assert completed.stage == Stage.COMPLETED
    assert completed.state["search_skipped"] == "materials_only"
    assert completed.state["search_queries"] == []
    assert completed.state["papers"] == []
    assert completed.state["evidence"][0]["paper_title"] == "local-notes.md"


async def test_materials_only_requires_local_material(settings) -> None:
    flow = ResearchWorkflow(settings, search=PaperSearchService([MemorySource([])]))
    project = flow.create_project(
        "验证只使用已有材料模式不会在没有材料时假装继续",
        configuration={"research_mode": "materials_only"},
    )

    with pytest.raises(RuntimeError, match="至少一份资料或结果"):
        await flow.run(project.id)


async def test_design_configuration_and_stale_outputs_are_guarded(settings) -> None:
    flow = offline_flow(settings)
    project = flow.create_project("验证机制图配置会从方法设计阶段安全失效")
    completed = await flow.run(project.id)
    flow.save_decision(
        project.id,
        decision_type="figure",
        item_id="figure-1",
        value="revise",
    )
    flow.prepare_delivery(project.id)

    updated = flow.update_configuration(
        project.id,
        {**completed.state["configuration"], "mechanism_figure": "omit"},
    )

    assert updated.stage == Stage.DESIGNING
    assert updated.state["pending_clear_from"] == "designing"
    assert "figure_story" not in updated.state
    assert not [item for item in updated.state["decisions"] if item.get("type") == "figure"]
    with pytest.raises(ValueError, match="待更新阶段"):
        flow.prepare_delivery(project.id)
    with pytest.raises(ValueError, match="待更新阶段"):
        flow.add_feedback(project.id, scope="manuscript", text="不要处理旧稿")


async def test_invalid_figure_decision_does_not_corrupt_artifact(settings) -> None:
    flow = offline_flow(settings)
    project = flow.create_project("验证非法图件选择不会提前改写开放制品")
    await flow.run(project.id)
    path = flow.store.artifact_path(project.id, "figure-story.json")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="keep、revise 或 omit"):
        flow.save_decision(
            project.id,
            decision_type="figure",
            item_id="figure-1",
            value="invalid",
        )

    assert path.read_bytes() == before


async def test_configuration_change_invalidates_delivery_outputs(settings) -> None:
    flow = offline_flow(settings)
    project = flow.create_project(
        "验证交付配置变化后不会继续暴露旧的交付包",
        configuration={"formats": ["md", "docx"]},
    )
    await flow.run(project.id)
    delivered = flow.prepare_delivery(project.id)
    assert delivered.state["delivery_manifest"]
    assert flow.store.artifact_path(project.id, "paper.docx") is not None
    assert flow.store.artifact_path(project.id, "delivery-package.zip") is not None

    updated = flow.update_configuration(
        project.id,
        {**delivered.state["configuration"], "formats": ["md", "tex"]},
    )
    assert "delivery_manifest" not in updated.state
    assert flow.store.artifact_path(project.id, "paper.docx") is None
    assert flow.store.artifact_path(project.id, "delivery-package.zip") is None
    assert flow.store.artifact_path(project.id, "paper.md") is not None


@pytest.mark.parametrize("scope", ["manuscript", "submission_package"])
async def test_external_delivery_scopes_only_package_requested_manuscript_formats(
    settings, scope
) -> None:
    flow = offline_flow(settings)
    project = flow.create_project(
        "验证对外交付范围不会包含证据摘录与内部审阅记录",
        configuration={"requested_scope": scope, "formats": ["md", "docx"]},
    )
    await flow.run(project.id)

    delivered = flow.prepare_delivery(project.id)
    with zipfile.ZipFile(flow.store.artifact_path(project.id, "delivery-package.zip")) as archive:
        names = set(archive.namelist())

    assert names == {"paper.md", "paper.docx", "delivery-manifest.json"}
    assert {item["name"] for item in delivered.state["delivery_manifest"]["files"]} == {
        "paper.md",
        "paper.docx",
    }
    assert "证据账本" in delivered.state["delivery_manifest"]["sharing_boundary"]


async def test_local_delivery_scope_includes_auditable_supporting_records(settings) -> None:
    flow = offline_flow(settings)
    project = flow.create_project(
        "验证本地完整包保留可审计的支持记录",
        configuration={"requested_scope": "local_delivery", "formats": ["md"]},
    )
    await flow.run(project.id)

    delivered = flow.prepare_delivery(project.id)
    files = {item["name"] for item in delivered.state["delivery_manifest"]["files"]}

    assert {"paper.md", "evidence.json", "figure-story.json", "final-review.json"} <= files
    assert "可能含上传材料摘录" in delivered.state["delivery_manifest"]["sharing_boundary"]


async def test_scoping_rerun_resets_stale_contribution_choice(settings) -> None:
    flow = offline_flow(settings)
    project = flow.create_project("验证重新界定范围后贡献选择与新研究规格保持一致")
    await flow.run(project.id)
    flow.save_decision(
        project.id,
        decision_type="contribution",
        item_id="validation",
        value="selected",
    )
    await flow.resume(project.id)

    rerun = await flow.rerun_from(project.id, Stage.SCOPING)

    assert rerun.state["selected_contribution"] == "primary"
    assert not [item for item in rerun.state["decisions"] if item.get("type") == "contribution"]
    assert rerun.state["spec"]["contribution"] == rerun.state["contribution_options"][0]["summary"]


def test_v2_api_configuration_decision_feedback_and_delivery(settings) -> None:
    flow = offline_flow(settings)
    with TestClient(create_app(flow)) as client:
        response = client.post(
            "/api/projects",
            json={
                "idea": "验证新版工作台接口可以持久保存研究配置",
                "run_now": False,
                "configuration": {
                    "workflow": "build_from_materials",
                    "scene": "conference",
                    "target_name": "TestConf",
                    "output_language": "en",
                    "research_mode": "materials_only",
                    "requested_scope": "local_delivery",
                    "author_voice": "strict",
                    "same_field_papers": 6,
                    "target_venue_papers": 6,
                    "reference_count_mode": "custom",
                    "reference_count": 30,
                    "mechanism_figure": "auto",
                    "formats": ["md", "tex"],
                },
            },
        )
        assert response.status_code == 201
        project_id = response.json()["id"]
        assert response.json()["state"]["configuration"]["target_name"] == "TestConf"
        invalid = client.post(
            f"/api/projects/{project_id}/decisions",
            json={
                "decision_type": "contribution",
                "item_id": "missing",
                "value": "selected",
            },
        )
        assert invalid.status_code == 422
        assert client.post(
            f"/api/projects/{project_id}/feedback",
            json={"scope": "manuscript", "text": "请修订"},
        ).status_code == 422
        assert client.post(f"/api/projects/{project_id}/delivery").status_code == 422


def test_api_rejects_unknown_configuration_fields(settings) -> None:
    with TestClient(create_app(offline_flow(settings))) as client:
        response = client.post(
            "/api/projects",
            json={
                "idea": "验证配置字段拼写错误不会静默改变研究边界",
                "run_now": False,
                "configuration": {"research_mod": "materials_only"},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_delivery_persists_default_configuration_for_legacy_project(settings) -> None:
    import asyncio

    flow = offline_flow(settings)
    project = flow.create_project("验证旧项目准备交付后会持久化新版默认配置")
    asyncio.run(flow.run(project.id))
    legacy = flow.store.get_project(project.id)
    legacy.state.pop("configuration")
    legacy.state.pop("schema_version")
    flow.store.save_project(legacy)

    with TestClient(create_app(flow)) as client:
        response = client.post(f"/api/projects/{project.id}/delivery")

    assert response.status_code == 200
    assert response.json()["state"]["configuration"] == normalize_configuration()
    assert flow.store.get_project(project.id).state["schema_version"] == 2


def test_web_home_exposes_eight_area_workspace(settings) -> None:
    with TestClient(create_app(offline_flow(settings))) as client:
        response = client.get("/")

    assert response.status_code == 200
    for identifier in (
        "configurationWorkbench",
        "materialsWorkbench",
        "contributionWorkbench",
        "evidenceWorkbench",
        "draftWorkbench",
        "figureWorkbench",
        "reviewWorkbench",
        "deliveryWorkbench",
    ):
        assert f'id="{identifier}"' in response.text
    assert 'name="deliveryFormats" value="docx"' in response.text
    assert 'class="brand-icon"' in response.text
    assert 'id="brandVersion" class="brand-version">v0.2.0' in response.text
    assert 'class="workspace-grid v2-workbench-grid"' in response.text
    for section in (
        "configurationWorkbench",
        "materialsWorkbench",
        "contributionWorkbench",
        "evidenceWorkbench",
        "draftWorkbench",
        "figureWorkbench",
        "reviewWorkbench",
        "deliveryWorkbench",
    ):
        assert f'data-section="{section}"' in response.text


def test_learning_and_figure_artifacts_are_json(settings) -> None:
    flow = offline_flow(settings)
    project = flow.create_project("验证新版规划制品都是可移植的开放 JSON 文件")
    import asyncio

    asyncio.run(flow.run(project.id))
    for name in ("learning-plan.json", "contribution-options.json", "figure-story.json"):
        value = json.loads(flow.store.artifact_path(project.id, name).read_text(encoding="utf-8"))
        assert value
