import pytest
from fastapi.testclient import TestClient

from scholaros.api import SearchPlanRevision, create_app
from scholaros.domain import MAX_RESEARCH_IDEA_LENGTH, ProjectStatus, Stage
from scholaros.papers import PaperSearchService
from scholaros.workflow import ResearchWorkflow


def test_long_idea_api_round_trip_without_truncation(settings):
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    idea = "研究背景🧪" * (MAX_RESEARCH_IDEA_LENGTH // 5 - 1) + "最后的约束"
    assert len(idea) == MAX_RESEARCH_IDEA_LENGTH
    with TestClient(create_app(workflow)) as client:
        response = client.post("/api/projects", json={"idea": idea, "run_now": False})
        assert response.status_code == 201
        saved = client.get(f"/api/projects/{response.json()['id']}").json()
        assert saved["idea"] == idea
        assert client.post("/api/projects", json={"idea": idea + "超", "run_now": False}).status_code == 422
        assert client.post(f"/api/projects/{saved['id']}/reject-search", json={"idea": idea + "超"}).status_code == 422


def test_long_revised_idea_uses_same_workflow_limit(settings):
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("需要修改的完整研究背景和问题")
    project.stage = Stage.SEARCHING
    project.status = ProjectStatus.NEEDS_ATTENTION
    project.state["search_confirmation_required"] = True
    workflow.store.save_project(project)
    idea = "研" * MAX_RESEARCH_IDEA_LENGTH
    assert SearchPlanRevision(idea=idea).idea == idea
    updated = workflow.reject_search_plan(project.id, revised_idea=idea)
    assert updated.idea == idea
    with pytest.raises(ValueError, match="100,000"):
        workflow.create_project(idea + "超")
