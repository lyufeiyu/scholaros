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
