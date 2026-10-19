from __future__ import annotations

import asyncio
import io
import json
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import scholaros.experiments as experiment_module
from scholaros.api import create_app
from scholaros.experiments import ExperimentProtocol, ExperimentService
from scholaros.papers import PaperSearchService
from scholaros.storage import ProjectBusyError
from scholaros.workflow import ResearchWorkflow


def protocol() -> dict:
    return {
        "hypothesis": "资源约束可以改善协作任务完成率",
        "intervention": "将共享资源上限调低",
        "metric": "cooperation_rate",
        "direction": "maximize",
        "seeds": [11, 23],
        "variants": [{}, {"resource_limit": 5}],
        "timeout_seconds": 10,
        "acknowledge_code_risk": True,
    }


def project_with_inputs(settings):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = flow.create_project("研究资源上限改变多智能体社会的协作", guided=True)
    service = ExperimentService(flow.store)
    service.upload(project.id, "dataset", b"agent,score\na,1\n")
    service.upload(project.id, "baseline", b"print('baseline')\n")
    service.upload(project.id, "candidate", b"print('candidate')\n")
    return flow, project, service


def test_protocol_rejects_invalid_trials():
    value = protocol()
    value["seeds"] = [11, 11]
    try:
        ExperimentProtocol(**value)
    except ValueError:
        pass
    else:
        raise AssertionError("重复种子不应被接受")
    value = protocol()
    value["variants"] = [{"alpha": float("nan")}]
    try:
        ExperimentProtocol(**value)
    except ValueError:
        pass
    else:
        raise AssertionError("非有限参数不应被接受")


def test_no_docker_never_executes_host_script(settings, monkeypatch):
    _, project, service = project_with_inputs(settings)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    try:
        service.prepare(project.id, ExperimentProtocol(**protocol()))
    except RuntimeError as exc:
        assert "不会在宿主机" in str(exc)
    else:
        raise AssertionError("缺少容器时不应执行实验")
    assert not service.status(project.id)["runs"]


def test_container_run_snapshot_and_result_adoption(settings, monkeypatch):
    flow, project, _ = project_with_inputs(settings)
    commands = []
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    def fake_docker(command, **kwargs):
        commands.append(command)
        assert command[0] == "docker"
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"sha256:" + b"a" * 64)
        assert "--network" in command and command[command.index("--network") + 1] == "none"
        assert "--read-only" in command and "no-new-privileges" in command
        mount = next(command[index + 1] for index, item in enumerate(command) if item == "-v" and ":/tmp/result.json:rw" in command[index + 1])
        output = Path(mount.rsplit(":/tmp/result.json:rw", 1)[0])
        assert stat.S_IMODE(output.stat().st_mode) == 0o666
        params = json.loads(next(part.split("=", 1)[1] for part in command if part.startswith("SCHOLAROS_PARAMS=")))
        output.write_text(json.dumps({"metrics": {"cooperation_rate": 0.5 + 0.1 * bool(params)}}))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_docker)
    with TestClient(create_app(flow)) as client:
        started = client.post(f"/api/projects/{project.id}/experiments/runs", json=protocol())
        assert started.status_code == 202
        for _ in range(100):
            status = client.get(f"/api/projects/{project.id}/experiments").json()
            if status["active"] is None:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("实验没有完成")
        report = status["runs"][0]
        assert report["status"] == "completed"
        assert report["image_id"] == "sha256:" + "a" * 64
        assert len(report["trials"]) == 6
        assert len(commands) == 7
        assert {trial["role"] for trial in report["trials"]} == {"baseline", "candidate"}
        original_hash = report["inputs"]["dataset"]
        changed = client.post(
            f"/api/projects/{project.id}/experiments/inputs/dataset",
            files={"file": ("new.csv", b"agent,score\nb,2\n", "text/csv")},
        )
        assert changed.status_code == 200
        assert status["runs"][0]["inputs"]["dataset"] == original_hash
        adopted = client.post(f"/api/projects/{project.id}/experiments/runs/{report['id']}/adopt")
        assert adopted.status_code == 200
        assert adopted.json()["role"] == "results"
        assert client.post(f"/api/projects/{project.id}/experiments/runs/{report['id']}/adopt").status_code == 200
        assert client.post(
            f"/api/projects/{project.id}/documents?role=results",
            files={"file": ("other.json", b'{"metrics":{"cooperation_rate":0.9}}')},
        ).status_code == 409
        assert flow.store.get_project(project.id).state["documents"][0]["role"] == "results"
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o600
            for path in (settings.artifacts_path / project.id / "experiments" / "runs" / report["id"] / "outputs").glob("*/result.json")
        )


def test_upload_rejects_wrong_type_and_missing_inputs(settings):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = flow.create_project("研究人类通过规则调整影响智能体协作")
    with TestClient(create_app(flow)) as client:
        wrong = client.post(
            f"/api/projects/{project.id}/experiments/inputs/baseline",
            files={"file": ("baseline.txt", b"print(1)")},
        )
        assert wrong.status_code == 415
        assert client.get(f"/api/projects/{project.id}/experiments").json()["runs"] == []
        home = client.get("/").text
        assert 'data-view="experiments"' in home
        assert 'id="experimentForm"' in home
        assert "计算实验</h3>" in home
        assert "候选方案相对基线的变化" in home


def test_tampered_report_cannot_be_adopted(settings, monkeypatch):
    flow, project, service = project_with_inputs(settings)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    def fake_docker(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"sha256:" + b"b" * 64)
        mount = next(command[index + 1] for index, item in enumerate(command) if item == "-v" and ":/tmp/result.json:rw" in command[index + 1])
        output = Path(mount.rsplit(":/tmp/result.json:rw", 1)[0])
        output.write_text('{"metrics":{"cooperation_rate":0.7}}')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_docker)
    run_id = service.prepare(project.id, ExperimentProtocol(**protocol()))
    service.execute(project.id, run_id)
    path = service._root(project.id) / "runs" / run_id / "report.json"
    report = json.loads(path.read_text())
    report["trials"][0]["value"] = 0.9
    path.write_text(json.dumps(report))
    with TestClient(create_app(flow)) as client:
        response = client.post(f"/api/projects/{project.id}/experiments/runs/{run_id}/adopt")
        assert response.status_code == 422
        assert "原始文件不符" in response.json()["detail"]


def test_timeout_removes_named_container(settings, monkeypatch):
    _, project, service = project_with_inputs(settings)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    cleaned = []

    def fake_docker(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"sha256:" + b"b" * 64)
        if command[1:3] == ["rm", "-f"]:
            cleaned.append(command[-1])
            return subprocess.CompletedProcess(command, 0)
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(subprocess, "run", fake_docker)
    run_id = service.prepare(project.id, ExperimentProtocol(**protocol()))
    service.execute(project.id, run_id)
    status = service.status(project.id)
    assert status["runs"][0]["status"] == "partial"
    assert len(cleaned) == 6
    assert all(name.startswith("scholaros-") for name in cleaned)


def test_fifo_result_rejected_without_reading(settings, monkeypatch):
    import os

    _, project, service = project_with_inputs(settings)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    def fake_docker(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"sha256:" + b"b" * 64)
        mount = next(command[index + 1] for index, item in enumerate(command) if item == "-v" and ":/tmp/result.json:rw" in command[index + 1])
        path = Path(mount.rsplit(":/tmp/result.json:rw", 1)[0])
        path.unlink()
        os.mkfifo(path)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_docker)
    run_id = service.prepare(project.id, ExperimentProtocol(**protocol()))
    service.execute(project.id, run_id)
    assert service.status(project.id)["runs"][0]["status"] == "partial"


def test_other_process_cannot_mark_running_experiment_interrupted(settings, monkeypatch):
    from scholaros.storage import ProjectStore

    flow, project, service = project_with_inputs(settings)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=b"sha256:" + b"c" * 64
        ),
    )
    run_id = service.prepare(project.id, ExperimentProtocol(**protocol()))
    other_service = ExperimentService(ProjectStore(settings))
    with flow.store.project_lock(project.id):
        other_service.recover_interrupted(project.id)
        assert other_service.status(project.id)["active"]["id"] == run_id
    other_service.recover_interrupted(project.id)
    assert other_service.status(project.id)["active"] is None
    assert other_service.status(project.id)["runs"][0]["status"] == "interrupted"


def test_nonfinite_comparison_cannot_be_adopted(settings, monkeypatch):
    _, project, service = project_with_inputs(settings)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=b"sha256:" + b"d" * 64
        ),
    )
    monkeypatch.setattr(
        ExperimentService, "_trial",
        lambda run_dir, role, variant, seed, study, image_id, trial_id, source_hash=None: {
            "id": trial_id, "role": role, "parameters": variant, "seed": seed,
            "status": "completed", "value": 1e308 if role == "baseline" else -1e308,
        },
    )
    run_id = service.prepare(project.id, ExperimentProtocol(**protocol()))
    service.execute(project.id, run_id)
    assert service.status(project.id)["runs"][0]["status"] == "failed"


def test_failed_cleanup_stops_more_trials_and_keeps_attention(settings, monkeypatch):
    _, project, service = project_with_inputs(settings)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    attempts = []

    def fake_docker(command, **kwargs):
        attempts.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"sha256:" + b"e" * 64)
        if command[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(command, 1)
        if command[1:3] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"scholaros-still-running\n")
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(subprocess, "run", fake_docker)
    run_id = service.prepare(project.id, ExperimentProtocol(**protocol()))
    service.execute(project.id, run_id)
    status = service.status(project.id)
    assert status["active"]["status"] == "cleanup_required"
    assert status["runs"][0]["status"] == "cleanup_required"
    assert len([command for command in attempts if command[1] == "run"]) == 1
    service.recover_interrupted(project.id)
    assert service.status(project.id)["active"] is not None


def test_api_holds_project_lock_between_prepare_and_execute(settings, monkeypatch):
    from scholaros.storage import ProjectStore

    flow, project, service = project_with_inputs(settings)
    other = ExperimentService(ProjectStore(settings))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    def fake_docker(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"sha256:" + b"f" * 64)
        mount = next(command[index + 1] for index, item in enumerate(command) if item == "-v" and ":/tmp/result.json:rw" in command[index + 1])
        Path(mount.rsplit(":/tmp/result.json:rw", 1)[0]).write_text(
            '{"metrics":{"cooperation_rate":0.7}}'
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_docker)
    app = create_app(flow)
    original = app.state.experiments._prepare_locked

    def check_handoff(project_id, experiment_protocol):
        run_id = original(project_id, experiment_protocol)
        other.recover_interrupted(project_id)
        assert other.status(project_id)["active"]["id"] == run_id
        return run_id

    monkeypatch.setattr(app.state.experiments, "_prepare_locked", check_handoff)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.id}/experiments/runs", json=protocol())
        assert response.status_code == 202
        for _ in range(100):
            status = client.get(f"/api/projects/{project.id}/experiments").json()
            if status["active"] is None:
                break
            time.sleep(0.02)
        assert status["runs"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_cancelled_start_keeps_lock_until_preparation_finishes(settings, monkeypatch):
    flow, project, service = project_with_inputs(settings)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=b"sha256:" + b"f" * 64
        ),
    )
    app = create_app(flow)
    original = app.state.experiments._prepare_locked
    started = threading.Event()
    release = threading.Event()

    def delayed_prepare(project_id, experiment_protocol):
        started.set()
        release.wait(timeout=5)
        return original(project_id, experiment_protocol)

    monkeypatch.setattr(app.state.experiments, "_prepare_locked", delayed_prepare)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        request = asyncio.create_task(
            client.post(f"/api/projects/{project.id}/experiments/runs", json=protocol())
        )
        try:
            assert await asyncio.to_thread(started.wait, 2)
            request.cancel()
            await asyncio.sleep(0)
            with pytest.raises(ProjectBusyError), flow.store.project_lock(project.id):
                pass
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
    assert service.status(project.id)["active"] is None
    assert service.status(project.id)["runs"][0]["status"] == "interrupted"


def test_folder_run_with_nested_main_and_optional_baseline(settings, monkeypatch):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = flow.create_project("验证多文件项目目录中的主文件执行路径")
    service = ExperimentService(flow.store)
    manifest = service.upload_folder(project.id, [
        ("scripts/main.py", b"from helpers.score import measure\n"),
        ("helpers/score.py", b"def measure(): return 1\n"),
        ("data/scenarios.csv", b"item,value\na,1\n"),
    ])
    assert len(manifest["files"]) == 3
    value = protocol()
    value.update(mode="folder", folder_id=manifest["id"], main_file="scripts/main.py", dataset_file="data/scenarios.csv",
                 command="python scripts/main.py --rounds 2", variants=[{}], seeds=[11])
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    def fake_docker(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"sha256:" + b"a" * 64)
        assert command[-4:] == ["python", "/input/scripts/main.py", "--rounds", "2"]
        assert "SCHOLAROS_DATASET=/input/data/scenarios.csv" in command
        assert "PYTHONPATH=/input" in command
        assert command[command.index("--workdir") + 1] == "/input"
        mount = next(command[i + 1] for i, token in enumerate(command) if token == "-v"
                     and ":/tmp/result.json:rw" in command[i + 1])
        Path(mount.rsplit(":/tmp/result.json:rw", 1)[0]).write_text(
            '{"metrics":{"cooperation_rate":0.7}}'
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_docker)
    run_id = service.prepare(project.id, ExperimentProtocol(**value))
    service.execute(project.id, run_id)
    report = service.status(project.id)["runs"][0]
    assert report["status"] == "completed"
    assert report["comparison"]["baseline_mean"] is None
    assert len(report["trials"]) == 1
    assert service.adopt(project.id, run_id, flow)["role"] == "results"


def test_folder_rejects_hidden_and_traversal_paths(settings):
    _, project, service = project_with_inputs(settings)
    for path in ("../secret.py", ".env", "pkg/.private.py", "/tmp/code.py", "pkg\\code.py"):
        with pytest.raises(ValueError):
            service.upload_folder(project.id, [(path, b"print(1)\n")])
    for command in ("python -c 'print(1)'", "sh main.py", "python main.py; rm -rf x"):
        with pytest.raises(ValueError):
            service.command_argv(command, "main.py")
    assert service.command_argv("python -m pkg.main --rounds 2", "pkg/main.py") == [
        "python", "-m", "pkg.main", "--rounds", "2"
    ]
    with pytest.raises(ValueError):
        service.command_argv("python -m other.main", "pkg/main.py")
    assert service.command_argv("python 'a;b.py'", "a;b.py") == ["python", "/input/a;b.py"]
    with pytest.raises(ValueError):
        service.command_argv("python main.py && echo unsafe", "main.py")


def test_folder_upload_and_model_command_suggestion(settings):
    from scholaros.runtime import ModelTurn

    class SuggestionModel:
        def __init__(self):
            self.calls = 0

        async def turn(self, messages, tools):
            self.calls += 1
            assert "scripts/main.py" in messages[-1].content
            assert "--rounds 2" in messages[-1].content
            assert "本地实验命令说明" in messages[-1].content
            return ModelTurn(content='{"command":"python scripts/main.py --rounds 2"}')

    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    model = SuggestionModel()
    flow.writer.model = model
    project = flow.create_project("验证上传代码目录与受控命令建议")
    with TestClient(create_app(flow)) as client:
        uploaded = client.post(
            f"/api/projects/{project.id}/experiments/folder",
            data={"paths": ["scripts/main.py", "pkg/helper.py", "README.md"]},
            files=[
                ("files", ("main.py", b"print('hello')\n", "text/x-python")),
                ("files", ("helper.py", b"VALUE = 1\n", "text/x-python")),
                ("files", ("README.md", "本地实验命令说明".encode(), "text/markdown")),
            ],
        )
        assert uploaded.status_code == 201
        assert sorted(uploaded.json()["files"]) == ["README.md", "pkg/helper.py", "scripts/main.py"]
        default = client.post(
            f"/api/projects/{project.id}/experiments/suggest-command",
            json={"folder_id": uploaded.json()["id"], "main_file": "scripts/main.py", "allow_model": False},
        )
        assert default.json() == {"command": "python scripts/main.py", "source": "default"}
        assert model.calls == 0
        suggested = client.post(
            f"/api/projects/{project.id}/experiments/suggest-command",
            json={"folder_id": uploaded.json()["id"], "main_file": "scripts/main.py", "reference_command": "python scripts/main.py --rounds 2",
                  "allow_model": True},
        )
        assert suggested.json() == {"command": "python scripts/main.py --rounds 2", "source": "model"}
        assert model.calls == 1
        icon = client.get("/favicon.ico", follow_redirects=True)
        assert icon.status_code == 200
        assert "image/svg+xml" in icon.headers["content-type"]
        assert "<svg" in icon.text


def test_model_suggestion_cannot_switch_entrypoint(settings):
    from scholaros.runtime import ModelTurn

    class UnsafeModel:
        async def turn(self, messages, tools):
            return ModelTurn(content='{"command":"python other.py; echo unsafe"}')

    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    flow.writer.model = UnsafeModel()
    project = flow.create_project("验证模型不能改变已选择的运行入口")
    service = ExperimentService(flow.store)
    manifest = service.upload_folder(project.id, [("main.py", b"print(1)\n"), ("other.py", b"print(2)\n")])
    with TestClient(create_app(flow)) as client:
        response = client.post(
            f"/api/projects/{project.id}/experiments/suggest-command",
            json={"folder_id": manifest["id"], "main_file": "main.py", "allow_model": True},
        )
        assert response.status_code == 422


def test_folder_mode_uses_selected_baseline(settings, monkeypatch):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = flow.create_project("验证项目目录内的基线与候选主文件独立运行")
    service = ExperimentService(flow.store)
    manifest = service.upload_folder(project.id, [
        ("main.py", b"print('candidate')\n"),
        ("baseline.py", b"print('baseline')\n"),
    ])
    value = protocol()
    value.update(mode="folder", folder_id=manifest["id"], main_file="main.py", baseline_file="baseline.py",
                 baseline_command="python baseline.py --config base.json",
                 command="python main.py", variants=[{}], seeds=[11])
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    def fake_docker(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"sha256:" + b"a" * 64)
        baseline = "/input/baseline.py" in command
        if baseline:
            assert command[-4:] == ["python", "/input/baseline.py", "--config", "base.json"]
        metric = 0.4 if baseline else 0.6
        mount = next(command[i + 1] for i, token in enumerate(command) if token == "-v"
                     and ":/tmp/result.json:rw" in command[i + 1])
        Path(mount.rsplit(":/tmp/result.json:rw", 1)[0]).write_text(
            json.dumps({"metrics": {"cooperation_rate": metric}})
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_docker)
    run_id = service.prepare(project.id, ExperimentProtocol(**value))
    service.execute(project.id, run_id)
    report = service.status(project.id)["runs"][0]
    assert report["status"] == "completed"
    assert [trial["role"] for trial in report["trials"]] == ["baseline", "candidate"]
    assert report["comparison"]["baseline_mean"] == 0.4
    assert report["comparison"]["candidate_mean"] == 0.6


def test_folder_version_must_match_confirmed_upload(settings, monkeypatch):
    _, project, service = project_with_inputs(settings)
    first = service.upload_folder(project.id, [("main.py", b"print(1)\n")])
    service.upload_folder(project.id, [("main.py", b"print(2)\n")])
    value = protocol()
    value.update(mode="folder", folder_id=first["id"], main_file="main.py", command="python main.py")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=b"sha256:" + b"a" * 64
        ),
    )
    with pytest.raises(ValueError, match="文件夹已更换"):
        service.prepare(project.id, ExperimentProtocol(**value))
    assert service.status(project.id)["runs"] == []


def test_folder_path_prefix_conflict_returns_validation_error(settings):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = flow.create_project("验证上传路径不允许同时成为文件与目录")
    with TestClient(create_app(flow)) as client:
        response = client.post(
            f"/api/projects/{project.id}/experiments/folder",
            data={"paths": ["foo.py", "foo.py/bar.py"]},
            files=[
                ("files", ("foo.py", b"print(1)", "text/x-python")),
                ("files", ("bar.py", b"print(2)", "text/x-python")),
            ],
        )
        assert response.status_code == 422
        assert "冲突" in response.json()["detail"]


def test_algorithm_optimization_keeps_original_and_compares_same_protocol(settings, monkeypatch):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = flow.create_project("比较同一数据与种子下的原版和候选算法")
    service = ExperimentService(flow.store)
    folder = service.upload_folder(project.id, [
        ("main.py", b"from algorithm import solve\nprint(solve())\n"),
        ("algorithm.py", b"def solve(): return 1\n"),
        ("data.csv", b"item,value\na,1\n"),
    ])
    original_hash = folder["files"]["algorithm.py"]
    proposal = service.save_optimization(
        project.id, folder["id"], "main.py", {"algorithm.py": original_hash},
        [{"path": "algorithm.py", "code": "def solve(): return 2\n"}],
        "减少重复计算", "保持接口的前提下改善算法",
    )
    value = protocol()
    value.update(mode="folder", folder_id=folder["id"], main_file="main.py",
                 command="python main.py", optimization_proposal_id=proposal["id"],
                 dataset_file="data.csv", variants=[{}, {"resource_limit": 5}], seeds=[11])
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    def fake_docker(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=b"sha256:" + b"a" * 64)
        mounted = next(command[i + 1] for i, token in enumerate(command)
                       if token == "-v" and ":/input:ro" in command[i + 1])
        source = Path(mounted.split(":/input:ro", 1)[0])
        params = json.loads(next(item.split("=", 1)[1] for item in command if item.startswith("SCHOLAROS_PARAMS=")))
        metric = (0.4 if "return 1" in (source / "algorithm.py").read_text() else 0.3) + (0.1 if params else 0)
        output = next(command[i + 1] for i, token in enumerate(command)
                      if token == "-v" and ":/tmp/result.json:rw" in command[i + 1])
        Path(output.split(":/tmp/result.json:rw", 1)[0]).write_text(
            json.dumps({"metrics": {"cooperation_rate": metric}})
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_docker)
    run_id = service.prepare(project.id, ExperimentProtocol(**value))
    service.execute(project.id, run_id)
    report = service.status(project.id)["runs"][0]
    assert report["status"] == "completed"
    assert [trial["role"] for trial in report["trials"]] == ["baseline", "baseline", "candidate", "candidate"]
    assert report["comparison"]["difference"] < 0
    assert report["comparison"]["parameters"] == {"resource_limit": 5}
    assert report["comparison"]["baseline_mean"] == 0.5
    assert report["trials"][0]["command"] == report["trials"][2]["command"]
    assert all(trial["source_sha256"] == report["optimized"]["original_tree_sha256"] for trial in report["trials"][:2])
    assert all(trial["source_sha256"] == report["optimized"]["candidate_tree_sha256"] for trial in report["trials"][2:])
    assert report["optimized"]["edits"][0]["path"] == "algorithm.py"
    assert service.folder_file(project.id, "algorithm.py").read_text() == "def solve(): return 1\n"
    report_path = settings.artifacts_path / project.id / "experiments" / "runs" / run_id / "report.json"
    altered = json.loads(json.dumps(report))
    altered["trials"][0]["command"] = ["python", "/input/other.py"]
    report_path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="命令与已确认协议不符"):
        service.adopt(project.id, run_id, flow)
    report_path.write_text(json.dumps(report))
    assert service.adopt(project.id, run_id, flow)["role"] == "results"


def test_optimization_requires_model_consent_and_valid_code(settings):
    from scholaros.runtime import ModelTurn

    class OptimizationModel:
        def __init__(self):
            self.response = json.dumps({"edits": [{"path": "solver.py", "code": "def solve(): return 2\n"}],
                                        "reason": "减少重复计算"})
            self.calls = 0

        async def turn(self, messages, tools):
            self.calls += 1
            assert "def solve(): return 1" in messages[-1].content
            return ModelTurn(content=self.response)

    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    model = OptimizationModel()
    flow.writer.model = model
    project = flow.create_project("验证优化候选须先审阅并保持原文件不变")
    service = ExperimentService(flow.store)
    folder = service.upload_folder(project.id, [
        ("main.py", b"from solver import solve\nprint(solve())\n"),
        ("solver.py", b"def solve(): return 1\n"),
    ])
    path = f"/api/projects/{project.id}/experiments/optimize"
    payload = {"folder_id": folder["id"], "main_file": "main.py",
               "files_to_consider": ["solver.py"], "goal": "减少重复计算"}
    with TestClient(create_app(flow)) as client:
        assert client.post(path, json=payload).status_code == 422
        assert model.calls == 0
        approved = client.post(path, json={**payload, "allow_model": True})
        assert approved.status_code == 200
        assert "+def solve(): return 2" in approved.json()["diff"]
        assert service.folder_file(project.id, "solver.py").read_text() == "def solve(): return 1\n"
        model.response = json.dumps({"edits": [{"path": "solver.py", "code": "def broken(:"}],
                                     "reason": "测试语法门槛"})
        invalid = client.post(path, json={**payload, "allow_model": True})
        assert invalid.status_code == 422
        model.response = "[]"
        assert client.post(path, json={**payload, "allow_model": True}).status_code == 422
        assert service.status(project.id)["optimization"]["id"] == approved.json()["id"]


def test_optimization_can_change_two_algorithm_files_without_changing_entrypoint(settings, monkeypatch):
    flow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = flow.create_project("比较多个算法模块改动与原版代码的差异")
    service = ExperimentService(flow.store)
    folder = service.upload_folder(project.id, [
        ("run.py", b"from algo.core import solve\nprint(solve())\n"),
        ("algo/core.py", b"def solve(): return 1\n"),
        ("algo/helper.py", b"def weight(): return 1\n"),
    ])
    selected = {path: folder["files"][path] for path in ("algo/core.py", "algo/helper.py")}
    proposal = service.save_optimization(project.id, folder["id"], "run.py", selected, [
        {"path": "algo/core.py", "code": "def solve(): return 2\n"},
        {"path": "algo/helper.py", "code": "def weight(): return 2\n"},
    ], "调整算法核心与权重函数", "减少重复计算")
    assert len(proposal["edits"]) == 2
    assert "候选/algo/core.py" in proposal["diff"]
    assert "候选/algo/helper.py" in proposal["diff"]
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=b"sha256:" + b"a" * 64
        ),
    )
    value = protocol()
    value.update(mode="folder", folder_id=folder["id"], main_file="run.py",
                 command="python run.py", optimization_proposal_id=proposal["id"],
                 variants=[{}], seeds=[11])
    run_id = service.prepare(project.id, ExperimentProtocol(**value))
    snapshot = settings.artifacts_path / project.id / "experiments" / "runs" / run_id / "inputs"
    assert (snapshot / "project" / "run.py").read_bytes() == (snapshot / "optimized" / "run.py").read_bytes()
    assert (snapshot / "project" / "algo/core.py").read_text() == "def solve(): return 1\n"
    assert (snapshot / "optimized" / "algo/core.py").read_text() == "def solve(): return 2\n"
    assert (snapshot / "optimized" / "algo/helper.py").read_text() == "def weight(): return 2\n"
    with pytest.raises(ValueError, match="未授权"):
        service.save_optimization(project.id, folder["id"], "run.py", selected,
                                  [{"path": "run.py", "code": "print('changed')\n"}], "越界", "减少重复计算")


def test_streamed_project_folder_rejects_total_over_limit(settings, monkeypatch):
    _, project, service = project_with_inputs(settings)
    monkeypatch.setattr(experiment_module, "MAX_FOLDER", 3)
    with pytest.raises(ValueError, match="2 GiB"):
        service.upload_folder_streamed(project.id, [("main.py", io.BytesIO(b"abcd"))])
    assert service.status(project.id)["folder"] is None
