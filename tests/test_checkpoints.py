import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scholaros.api import create_app
from scholaros.cli import build_parser
from scholaros.domain import Paper, ProjectStatus, ResearchSpec, Stage
from scholaros.papers import MemorySource, PaperSearchService
from scholaros.storage import ProjectBusyError
from scholaros.workflow import ResearchWorkflow
from scholaros.writing import ResearchWriter


class CountingWriter(ResearchWriter):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail_design = False

    async def scope(self, idea, documents=()):
        self.calls.append("scoping")
        return ResearchSpec("研究智能体", idea, "可检验研究", ["H1"], ["research agents"])

    async def synthesize(self, *args):
        self.calls.append("synthesizing")
        return await super().synthesize(*args)

    async def design(self, *args):
        self.calls.append("designing")
        if self.fail_design:
            raise RuntimeError("测试中断")
        return await super().design(*args)

    async def draft(self, *args):
        self.calls.append("drafting")
        return await super().draft(*args)


@pytest.fixture
def flow(settings):
    source = MemorySource([Paper(
        title="Research agents", authors=["Ada Smith"], year=2025,
        abstract="Research agents and traceable evidence.", sources=["memory"],
        external_id="paper-1", doi="10.1000/research",
    )])
    return ResearchWorkflow(settings, search=PaperSearchService([source]), writer=CountingWriter())


async def test_failed_resume_preserves_upstream_and_legacy_state(flow):
    project = flow.create_project("验证中断后保留已经完成的研究阶段")
    flow.writer.fail_design = True
    with pytest.raises(RuntimeError, match="测试中断"):
        await flow.run(project.id)
    saved = flow.store.get_project(project.id)
    assert saved.stage == Stage.DESIGNING
    papers = flow.store.artifact_path(project.id, "papers.json").read_bytes()
    # 旧项目没有新字段也能继续。
    saved.state.pop("completed_stages")
    saved.state.pop("schema_version")
    flow.store.save_project(saved)
    flow.writer.fail_design = False
    result = await flow.resume(project.id)
    assert result.stage == Stage.COMPLETED
    assert flow.writer.calls.count("scoping") == 1
    assert flow.writer.calls.count("synthesizing") == 1
    assert flow.store.artifact_path(project.id, "papers.json").read_bytes() == papers


async def test_stale_running_can_resume_but_active_lock_cannot(flow):
    project = flow.create_project("验证旧运行状态可以在安全获取锁后继续")
    project.status = ProjectStatus.RUNNING
    flow.store.save_project(project)
    with flow.store.project_lock(project.id), pytest.raises(ProjectBusyError):
        await flow.resume(project.id)
    assert flow.store.get_project(project.id).status == ProjectStatus.RUNNING
    assert (await flow.resume(project.id)).stage == Stage.COMPLETED


async def test_guided_checkpoints_do_not_rerun_approved_stages(flow):
    project = flow.create_project("验证引导式研究流程每个阶段都可人工确认", guided=True)
    pending = await flow.run(project.id)
    assert pending.state["pending_checkpoint"] == "scoping"
    assert pending.stage == Stage.SEARCHING
    assert (await flow.resume(project.id)).state["pending_checkpoint"] == "scoping"
    assert flow.writer.calls == ["scoping"]
    pending = await flow.approve_and_run(project.id)
    assert pending.state["search_confirmation_required"] is True
    pending = await flow.confirm_search_plan_and_run(project.id)
    for stage in ("synthesizing", "designing", "drafting"):
        assert pending.state["pending_checkpoint"] == stage
        pending = await flow.approve_and_run(project.id)
    assert pending.stage == Stage.COMPLETED
    assert flow.writer.calls == ["scoping", "synthesizing", "designing", "drafting"]
    with pytest.raises(ValueError, match="没有待确认"):
        await flow.approve_and_run(project.id)


async def test_rerun_preserves_upstream_and_archives_old_outputs(flow):
    project = flow.create_project("验证方法局部重做不会改动已完成的检索与证据")
    await flow.run(project.id)
    old_paper = flow.store.artifact_path(project.id, "paper.md").read_bytes()
    upstream = {name: flow.store.artifact_path(project.id, name).read_bytes()
                for name in ("papers.json", "evidence.json")}
    await flow.rerun_from(project.id, "designing")
    assert flow.writer.calls.count("scoping") == 1
    assert flow.writer.calls.count("synthesizing") == 1
    assert flow.writer.calls.count("designing") == 2
    for name, data in upstream.items():
        assert flow.store.artifact_path(project.id, name).read_bytes() == data
    history = flow.store.list_history(project.id)
    assert len(history) == 1
    assert flow.store.history_artifact_path(project.id, history[0]["revision"], "paper.md").read_bytes() == old_paper
    manifest = json.loads(flow.store.history_artifact_path(project.id, history[0]["revision"], "manifest.json").read_text())
    assert manifest["project"]["stage"] == "completed"
    assert len(manifest["artifacts"]["paper.md"]) == 64


async def test_snapshot_failure_does_not_reset_or_delete_current_project(flow, monkeypatch):
    project = flow.create_project("验证历史快照失败不会删除现有论文")
    await flow.run(project.id)
    before = flow.store.get_project(project.id).to_dict()
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(flow.store, "snapshot_project", fail)
    with pytest.raises(OSError, match="disk full"):
        await flow.run(project.id, restart=True)
    assert flow.store.get_project(project.id).to_dict() == before
    assert flow.store.artifact_path(project.id, "paper.md").is_file()


async def test_new_results_keep_search_and_invalidate_downstream(flow, tmp_path):
    project = flow.create_project("验证新增结果时仍可保留已经检索的论文")
    await flow.run(project.id)
    source = tmp_path / "results.md"
    source.write_text("实验尚待核验。", encoding="utf-8")
    flow.add_document(project.id, source, role="results")
    saved = flow.store.get_project(project.id)
    assert saved.stage == Stage.SYNTHESIZING
    assert "papers" in saved.state and "design" not in saved.state
    with pytest.raises(ValueError):
        await flow.rerun_from(project.id, Stage.DRAFTING)
    await flow.resume(project.id)
    assert flow.writer.calls.count("scoping") == 1
    assert len(flow.store.list_history(project.id)) == 1


async def test_invalid_rerun_is_non_mutating(flow):
    project = flow.create_project("验证不能跳过缺失的上游研究阶段")
    before = flow.store.get_project(project.id).to_dict()
    with pytest.raises(ValueError, match="上游"):
        await flow.rerun_from(project.id, Stage.DRAFTING)
    assert flow.store.get_project(project.id).to_dict() == before
    assert flow.store.list_history(project.id) == []


async def test_failed_final_review_resume_does_not_restart(flow):
    project = flow.create_project("验证终审待处理状态不会误从第一阶段重跑")
    await flow.run(project.id)
    saved = flow.store.get_project(project.id)
    saved.status = ProjectStatus.NEEDS_ATTENTION
    flow.store.save_project(saved)
    before = list(flow.writer.calls)
    assert (await flow.resume(project.id)).stage == Stage.COMPLETED
    assert flow.writer.calls == before


def test_atomic_write_keeps_previous_artifact_on_replace_failure(flow, monkeypatch):
    project = flow.create_project("验证写入失败时保留上一版完整制品")
    path = flow.store.save_artifact(project.id, "paper.md", "原文")
    def fail(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        flow.store.save_artifact(project.id, "paper.md", "新文")
    assert path.read_text() == "原文"
    assert not list(path.parent.glob(".*.tmp"))


def test_recovery_api_respects_locks_and_history_paths(flow):
    project = flow.create_project("验证新恢复接口和历史版本下载边界")
    asyncio.run(flow.run(project.id))
    asyncio.run(flow.rerun_from(project.id, Stage.REVIEWING))
    with TestClient(create_app(flow)) as client:
        history = client.get(f"/api/projects/{project.id}/history").json()
        revision = history[0]["revision"]
        assert client.get(f"/api/projects/{project.id}/history/{revision}/paper.md").status_code == 200
        assert client.get(f"/api/projects/{project.id}/history/{revision}/.env").status_code == 404
        assert client.get(f"/api/projects/{project.id}/history/invalid/paper.md").status_code == 404
        assert client.post(f"/api/projects/{project.id}/approve").status_code == 422
        assert client.post(f"/api/projects/{project.id}/rerun?stage=completed").status_code == 422
        with flow.store.project_lock(project.id):
            for action in ("resume", "rerun?stage=designing", "approve"):
                assert client.post(f"/api/projects/{project.id}/{action}").status_code == 409


def test_cli_supports_guided_and_recovery_commands():
    assert build_parser().parse_args(["run", "test idea", "--guided"]).guided
    assert build_parser().parse_args(["rerun", "0123456789ab", "--from-stage", "drafting"]).from_stage == "drafting"
    for action in ("resume", "approve", "history"):
        assert build_parser().parse_args([action, "0123456789ab"]).command == action


async def test_rerun_cannot_skip_pending_confirmation(flow):
    project = flow.create_project("验证局部重做不能绕过人工确认", guided=True)
    await flow.run(project.id)
    with pytest.raises(ValueError, match="人工确认"):
        await flow.rerun_from(project.id, Stage.SEARCHING)
    assert flow.store.list_history(project.id) == []
    await flow.approve_and_run(project.id)
    with pytest.raises(ValueError, match="外发查询确认"):
        await flow.rerun_from(project.id, Stage.SYNTHESIZING)


async def test_rerun_invalidates_only_downstream_approvals(flow):
    project = flow.create_project("验证历史确认不再代表重做后的新结果", guided=True)
    await flow.run(project.id)
    await flow.approve_and_run(project.id)
    await flow.confirm_search_plan_and_run(project.id)
    await flow.approve_and_run(project.id)
    await flow.approve_and_run(project.id)
    await flow.approve_and_run(project.id)
    result = await flow.rerun_from(project.id, Stage.DESIGNING)
    assert result.state["pending_checkpoint"] == "designing"
    assert [item["stage"] for item in result.state["approvals"]] == ["scoping", "synthesizing"]


async def test_rejected_plan_snapshot_records_original_idea(flow):
    project = flow.create_project("这是修改前的完整研究问题", guided=True)
    await flow.run(project.id)
    await flow.approve_and_run(project.id)
    updated = flow.reject_search_plan(project.id, revised_idea="这是人工重新修改后的研究问题")
    revision = flow.store.list_history(project.id)[0]["revision"]
    manifest = json.loads(flow.store.history_artifact_path(project.id, revision, "manifest.json").read_text())
    assert manifest["project"]["idea"] == project.idea
    assert updated.idea != project.idea
    assert updated.state["clear_generated_on_next_run"] is True


async def test_offline_guided_project_stays_offline_in_new_workflow(settings, monkeypatch):
    from scholaros.llm import OpenAICompatibleModel

    async def must_not_call_model(*args, **kwargs):
        raise AssertionError("离线项目不得调用模型 API")

    monkeypatch.setenv(settings.api_key_env, "test-only-fake-key")
    monkeypatch.setattr(OpenAICompatibleModel, "turn", must_not_call_model)
    offline = ResearchWorkflow(settings, search=PaperSearchService([]), allow_empty_search=True)
    project = offline.create_project("验证离线项目跨进程继续时不会发送网络请求", guided=True)
    await offline.run(project.id)

    class MustNotRunWriter(ResearchWriter):
        async def synthesize(self, *args):
            raise AssertionError("错误地使用了联网工作流")

    fresh = ResearchWorkflow(settings, writer=MustNotRunWriter())
    pending = await fresh.approve_and_run(project.id)
    assert pending.state["offline"] is True
    assert pending.state["pending_checkpoint"] == "synthesizing"
    assert not pending.state.get("search_confirmation_required")
    for stage in ("designing", "drafting"):
        pending = await fresh.approve_and_run(project.id)
        assert pending.state["pending_checkpoint"] == stage
    assert (await fresh.approve_and_run(project.id)).stage == Stage.COMPLETED
    restarted = await fresh.run(project.id, restart=True)
    assert restarted.state["offline"] is True
    assert restarted.state["pending_checkpoint"] == "scoping"


async def test_empty_search_cannot_be_bypassed_by_partial_rerun(settings):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]), writer=CountingWriter())
    project = flow.create_project("验证检索失败后不能用局部重做绕过空结果阻断")
    with pytest.raises(RuntimeError, match="没有检索到"):
        await flow.run(project.id)
    assert flow.store.get_project(project.id).state["papers"] == []
    with pytest.raises(ValueError, match="上游阶段尚未成功"):
        await flow.rerun_from(project.id, Stage.SYNTHESIZING)
    assert flow.store.list_history(project.id) == []


def _wait_for_idle(client, project_id):
    for _ in range(200):
        response = client.get(f"/api/projects/{project_id}")
        assert response.status_code == 200
        project = response.json()
        if not project["is_active"]:
            return project
        time.sleep(0.01)
    pytest.fail("测试后台任务未在预期时间内结束")


def test_guided_api_runs_approvals_and_partial_rerun(flow):
    with TestClient(create_app(flow)) as client:
        created = client.post("/api/projects", json={
            "idea": "通过网页接口逐步确认并局部重做研究流程", "guided": True,
        })
        assert created.status_code == 201
        project_id = created.json()["id"]
        assert _wait_for_idle(client, project_id)["state"]["pending_checkpoint"] == "scoping"
        assert client.post(f"/api/projects/{project_id}/approve").status_code == 202
        assert _wait_for_idle(client, project_id)["state"]["search_confirmation_required"]
        assert client.post(f"/api/projects/{project_id}/confirm-search").status_code == 202
        for stage in ("synthesizing", "designing", "drafting"):
            assert _wait_for_idle(client, project_id)["state"]["pending_checkpoint"] == stage
            assert client.post(f"/api/projects/{project_id}/approve").status_code == 202
        assert _wait_for_idle(client, project_id)["stage"] == "completed"
        assert client.post(f"/api/projects/{project_id}/rerun?stage=designing").status_code == 202
        assert _wait_for_idle(client, project_id)["state"]["pending_checkpoint"] == "designing"
        assert len(client.get(f"/api/projects/{project_id}/history").json()) == 1
    assert flow.writer.calls.count("scoping") == 1
    assert flow.writer.calls.count("synthesizing") == 1


def test_api_failure_then_resume_keeps_completed_stages(flow):
    flow.writer.fail_design = True
    with TestClient(create_app(flow)) as client:
        project_id = client.post("/api/projects", json={"idea": "验证 API 后台失败后可以从断点安全恢复"}).json()["id"]
        failed = _wait_for_idle(client, project_id)
        assert failed["status"] == "failed" and failed["stage"] == "designing"
        assert "测试中断" in failed["error"]
        flow.writer.fail_design = False
        assert client.post(f"/api/projects/{project_id}/resume").status_code == 202
        assert _wait_for_idle(client, project_id)["stage"] == "completed"
    assert flow.writer.calls.count("scoping") == 1


def test_cli_offline_guided_round_trip(settings, monkeypatch, capsys):
    import sys

    from scholaros.cli import main
    from scholaros.config import Settings

    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["scholaros", "run", "验证命令行离线引导流程兼容原有入口", "--guided", "--offline", "--json"])
    main()
    project = json.loads(capsys.readouterr().out)["project"]
    assert project["state"]["pending_checkpoint"] == "scoping"
    for expected in ("synthesizing", "designing", "drafting", None):
        monkeypatch.setattr(sys, "argv", ["scholaros", "approve", project["id"]])
        main()
        project = json.loads(capsys.readouterr().out)
        assert project["state"].get("pending_checkpoint") == expected
    assert project["stage"] == "completed"
