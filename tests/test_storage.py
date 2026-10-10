from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from scholaros import storage
from scholaros.domain import Project
from scholaros.storage import ProjectBusyError, ProjectStore

PROJECT_ID = "123456abcdef"
OTHER_PROJECT_ID = "abcdef123456"
ROOT = Path(__file__).resolve().parents[1]
CHILD_LOCK_SCRIPT = """
import json
import os
import sys
from pathlib import Path
from scholaros.config import Settings
from scholaros.storage import ProjectBusyError, ProjectStore

config = json.loads(sys.argv[1])
config["home"] = Path(config["home"])
store = ProjectStore(Settings(**config))
try:
    with store.project_lock(sys.argv[2]):
        if sys.argv[3] == "exit":
            os._exit(0)
except ProjectBusyError:
    sys.exit(2)
"""


def _child_lock(settings, *, exit_without_cleanup: bool = False):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [
            sys.executable,
            "-c",
            CHILD_LOCK_SCRIPT,
            json.dumps(asdict(settings), default=str),
            PROJECT_ID,
            "exit" if exit_without_cleanup else "normal",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


def test_project_lock_rejects_same_project_without_releasing_owner(settings) -> None:
    first = ProjectStore(settings)
    second = ProjectStore(settings)
    with first.project_lock(PROJECT_ID):
        for _ in range(2):
            with pytest.raises(ProjectBusyError), second.project_lock(PROJECT_ID):
                pytest.fail("同一项目不能被重复加锁")
        with second.project_lock(OTHER_PROJECT_ID):
            pass
    with second.project_lock(PROJECT_ID):
        pass


def test_project_lock_releases_after_body_exception(settings) -> None:
    store = ProjectStore(settings)
    with pytest.raises(RuntimeError, match="测试异常"), store.project_lock(PROJECT_ID):
        raise RuntimeError("测试异常")
    with store.project_lock(PROJECT_ID):
        pass


def test_project_lock_rejects_other_process_then_allows_retry(settings) -> None:
    store = ProjectStore(settings)
    with store.project_lock(PROJECT_ID):
        result = _child_lock(settings)
        assert result.returncode == 2, result.stderr
    result = _child_lock(settings)
    assert result.returncode == 0, result.stderr


def test_project_lock_releases_when_process_exits_without_cleanup(settings) -> None:
    result = _child_lock(settings, exit_without_cleanup=True)
    assert result.returncode == 0, result.stderr
    with ProjectStore(settings).project_lock(PROJECT_ID):
        pass


@pytest.mark.parametrize("failure_on_unlock", [False, True])
def test_project_lock_closes_handle_and_preserves_system_errors(
    settings, monkeypatch, failure_on_unlock: bool
) -> None:
    store = ProjectStore(settings)
    handles = []
    calls = []
    original_open = Path.open
    if os.name == "nt":
        backend = storage.msvcrt
        function_name = "locking"
        acquire_mode = backend.LK_NBLCK
        release_mode = backend.LK_UNLCK
    else:
        backend = storage.fcntl
        function_name = "flock"
        acquire_mode = backend.LOCK_EX | backend.LOCK_NB
        release_mode = backend.LOCK_UN
    original_lock = getattr(backend, function_name)
    error = OSError(errno.EBADF, "测试系统错误")

    def tracked_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        handles.append(handle)
        return handle

    def failing_lock(fd, mode, *args):
        calls.append(mode)
        if not failure_on_unlock or mode == release_mode:
            raise error
        return original_lock(fd, mode, *args)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", tracked_open)
        patch.setattr(backend, function_name, failing_lock)
        with pytest.raises(OSError) as caught, store.project_lock(PROJECT_ID):
            pass
    assert caught.value is error
    assert calls == ([acquire_mode, release_mode] if failure_on_unlock else [acquire_mode])
    assert len(handles) == 1
    assert handles[0].closed
    with store.project_lock(PROJECT_ID):
        pass


def test_cli_help_starts_on_current_platform() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "scholaros.cli", "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "serve" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows 目录重命名锁定行为")
@pytest.mark.parametrize("winerror", [5, 32])
def test_snapshot_retries_transient_windows_directory_lock(settings, monkeypatch, winerror) -> None:
    store = ProjectStore(settings)
    project = Project(id=PROJECT_ID, idea="测试历史快照重试")
    store.save_artifact(PROJECT_ID, "paper.md", "测试论文")
    original_rename = Path.rename
    attempts = 0
    delays = []

    def intermittently_locked(path, target):
        nonlocal attempts
        if path.parent.name == "history" and path.name.endswith(".tmp"):
            attempts += 1
            if attempts < 3:
                raise OSError(0, "文件被暂时占用", str(path), winerror)
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", intermittently_locked)
    monkeypatch.setattr(storage.time, "sleep", delays.append)
    revision = store.snapshot_project(project, "阶段完成")
    assert attempts == 3
    assert delays == [0.1, 0.2]
    assert store.history_artifact_path(PROJECT_ID, revision, "paper.md").read_text(encoding="utf-8") == "测试论文"
    assert not list((settings.artifacts_path / PROJECT_ID / "history").glob(".*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows 目录重命名锁定行为")
def test_snapshot_permission_error_exhausts_retries_without_partial_history(settings, monkeypatch) -> None:
    store = ProjectStore(settings)
    project = Project(id=PROJECT_ID, idea="测试持久权限失败")
    attempts = 0
    original_rename = Path.rename

    def denied(path, target):
        nonlocal attempts
        if path.parent.name == "history" and path.name.endswith(".tmp"):
            attempts += 1
            raise OSError(0, "拒绝访问", str(path), 5)
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", denied)
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError, match="拒绝访问"):
        store.snapshot_project(project, "阶段完成")
    assert attempts == 5
    assert store.list_history(PROJECT_ID) == []
    assert not list((settings.artifacts_path / PROJECT_ID / "history").glob(".*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows 目录重命名锁定行为")
def test_snapshot_cleanup_retries_without_masking_original_failure(settings, monkeypatch) -> None:
    store = ProjectStore(settings)
    project = Project(id=PROJECT_ID, idea="测试快照清理")
    original_rmtree = storage.shutil.rmtree
    cleanups = 0

    def denied_rename(path, target):
        raise OSError(0, "拒绝访问", str(path), 5)

    def temporarily_denied_cleanup(path, *args, **kwargs):
        nonlocal cleanups
        cleanups += 1
        if cleanups < 3:
            raise OSError(0, "暂时占用", str(path), 32)
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", denied_rename)
    monkeypatch.setattr(storage.shutil, "rmtree", temporarily_denied_cleanup)
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError, match="可从断点继续") as caught:
        store.snapshot_project(project, "阶段完成")
    assert caught.value.__cause__.winerror == 5
    assert cleanups == 3
    assert not list((settings.artifacts_path / PROJECT_ID / "history").glob(".*.tmp"))


def test_snapshot_prunes_only_old_known_temporary_directories(settings) -> None:
    store = ProjectStore(settings)
    root = settings.artifacts_path / PROJECT_ID / "history"
    root.mkdir(parents=True)
    stale = root / ("." + "a" * 32 + ".tmp")
    recent = root / ("." + "b" * 32 + ".tmp")
    unrelated = root / "keep-user-files"
    for path in (stale, recent, unrelated):
        path.mkdir()
        (path / "note.txt").write_text("content", encoding="utf-8")
    old = storage.time.time() - 25 * 3600
    os.utime(stale, (old, old))
    os.utime(unrelated, (old, old))
    store.snapshot_project(Project(id=PROJECT_ID, idea="测试过期清理"), "阶段完成")
    assert not stale.exists()
    assert recent.is_dir()
    assert unrelated.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows 目录重命名错误码")
def test_snapshot_does_not_retry_unrelated_windows_error(settings, monkeypatch) -> None:
    store = ProjectStore(settings)
    attempts = 0

    def unrelated_error(path, target):
        nonlocal attempts
        attempts += 1
        raise OSError(0, "目标目录已存在", str(path), 183)

    monkeypatch.setattr(Path, "rename", unrelated_error)
    monkeypatch.setattr(storage.time, "sleep", lambda _: pytest.fail("不应重试目标冲突"))
    with pytest.raises(FileExistsError):
        store.snapshot_project(Project(id=PROJECT_ID, idea="测试错误码"), "阶段完成")
    assert attempts == 1
    assert not list((settings.artifacts_path / PROJECT_ID / "history").glob(".*.tmp"))
