"""完成稿定向修订不影响其他章节，并同步维护制品和历史。"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from scholaros.api import create_app
from scholaros.domain import ProjectStatus, Stage
from scholaros.manuscript_sections import ManuscriptSectionService
from scholaros.papers import PaperSearchService
from scholaros.runtime import ModelTurn
from scholaros.workflow import ResearchWorkflow

PAPER = ("# A Research Study\n\n## Abstract\nThis abstract reports an unverified plan.\n\n"
         "## Introduction\nThe original introduction explains the question and context.\n\n"
         "## Methods\nThe original methods describe the selected pipeline.\n\n"
         "## Results\nResults are pending independent verification.\n\n"
         "## Discussion\nThe discussion states cautious interpretations.\n\n"
         "## Conclusion\nThe conclusion is unchanged.\n\n")


class SectionModel:
    async def turn(self, messages, tools):
        assert tools == []
        return ModelTurn(content="The revised methods describe a reproducible pipeline with explicit inputs and validation boundaries.")


def finished_project(settings):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = flow.create_project(
        "Evaluate a reproducible research method with verified evidence.",
        configuration={"output_language": "en", "latex_template": "ieee_journal"},
    )
    project.stage = Stage.COMPLETED
    project.status = ProjectStatus.COMPLETED
    project.state["design"] = {"datasets": ["Dataset A"], "baselines": ["Baseline A"], "metrics": ["Accuracy"]}
    flow.store.save_project(project)
    flow.store.save_artifact(project.id, "paper.md", PAPER)
    flow.store.save_artifact(project.id, "paper.tex", "old tex")
    flow.store.save_artifact(project.id, "delivery-package.zip", b"old package")
    return flow, project


@pytest.mark.asyncio
async def test_only_selected_section_changes_and_old_package_is_invalidated(settings):
    flow, project = finished_project(settings)
    flow.writer.model = SectionModel()
    service = ManuscriptSectionService(flow)
    before = service.list(project.id)
    methods = next(section for section in before["sections"] if section["heading"] == "Methods")
    suggestion = await service.suggest(project.id, index=methods["index"], base_hash=before["base_hash"],
                                       instruction="Clarify pipeline inputs")
    after = service.apply(project.id, index=methods["index"], base_hash=before["base_hash"],
                          content=suggestion["candidate"], instruction="Clarify pipeline inputs")
    revised = flow.store.artifact_path(project.id, "paper.md").read_text()
    assert "The revised methods" in revised
    assert "The original methods" not in revised
    assert "The original introduction" in revised and "The conclusion is unchanged" in revised
    assert len(after["sections"]) == len(before["sections"])
    assert flow.store.artifact_path(project.id, "paper.tex") is not None
    assert "\\documentclass[lettersize,journal]{IEEEtran}" in flow.store.artifact_path(project.id, "paper.tex").read_text()
    assert flow.store.artifact_path(project.id, "delivery-package.zip") is None
    assert flow.store.artifact_path(project.id, "final-review.json") is not None
    assert flow.store.list_history(project.id)
    with pytest.raises(RuntimeError, match="已更新"):
        service.apply(project.id, index=methods["index"], base_hash=before["base_hash"],
                      content=suggestion["candidate"], instruction="again")


def test_section_api_requires_completed_paper_and_model(settings):
    flow, project = finished_project(settings)
    app = create_app(flow)
    with TestClient(app) as client:
        listed = client.get(f"/api/projects/{project.id}/manuscript/sections")
        assert listed.status_code == 200
        index = next(item["index"] for item in listed.json()["sections"] if item["heading"] == "Methods")
        payload = {"index": index, "base_hash": hashlib.sha256(PAPER.encode()).hexdigest(),
                   "instruction": "Clarify inputs"}
        assert client.post(f"/api/projects/{project.id}/manuscript/sections/suggest", json=payload).status_code == 409
        missing = flow.create_project("This project has no paper yet.")
        assert client.get(f"/api/projects/{missing.id}/manuscript/sections").status_code == 422


def test_unsupported_results_are_saved_for_review_but_block_delivery(settings):
    flow, project = finished_project(settings)
    service = ManuscriptSectionService(flow)
    listed = service.list(project.id)
    section = next(item for item in listed["sections"] if item["heading"] == "Results")
    service.apply(project.id, index=section["index"], base_hash=listed["base_hash"],
                  content="Our experiments show accuracy of 98.7% and demonstrate superiority over all baselines.",
                  instruction="Check results")
    updated = flow.store.get_project(project.id)
    assert updated.status == ProjectStatus.NEEDS_ATTENTION
    assert updated.state["section_review_blocked"] is True
    assert any(item["code"] == "fabricated_result_risk" for item in updated.state["final_review"]["findings"])
    assert flow.prepare_delivery(project.id).state["delivery_manifest"]["ready"] is False


def test_ieee_chinese_candidate_cannot_bypass_suggestion(settings):
    flow, project = finished_project(settings)
    service = ManuscriptSectionService(flow)
    listed = service.list(project.id)
    with pytest.raises(ValueError, match="IEEE 期刊章节需要英文"):
        service.apply(project.id, index=0, base_hash=listed["base_hash"],
                      content="本节是足够长的中文摘要，需要回到英文论文里写明研究问题和方法边界。", instruction="rewrite")
    assert flow.store.artifact_path(project.id, "paper.md").read_text() == PAPER


def test_markdown_fenced_headings_do_not_create_false_sections():
    text = "# Title\n\n## Methods\n```python\n## Not a heading\n```\n\n## Conclusion\nEnd.\n"
    sections = ManuscriptSectionService._sections(text)
    assert [item["heading"] for item in sections] == ["Methods", "Conclusion"]
    assert "## Not a heading" in sections[0]["content"]
