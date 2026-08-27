import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _launcher_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    project_dir = tmp_path / "ScholarOS project with spaces"
    project_dir.mkdir()
    launcher = project_dir / "scholaros.sh"
    shutil.copy2(ROOT / "scholaros.sh", launcher)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    return launcher, fake_bin, log


def _launcher_env(fake_bin: Path, log: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["SCHOLAROS_TEST_LOG"] = str(log)
    return env


def test_conda_environment_declares_project_and_dev_dependencies() -> None:
    config = (ROOT / "environment.yml").read_text(encoding="utf-8")

    assert "name: scholaros" in config
    assert "python=3.12" in config
    assert "-e .[dev]" in config


def test_launcher_uses_named_conda_environment_not_venv() -> None:
    launcher = (ROOT / "scholaros.sh").read_text(encoding="utf-8")

    assert "SCHOLAROS_CONDA_ENV" in launcher
    assert "conda run --no-capture-output" in launcher
    assert ", scholaros" in launcher
    assert "SCHOLAROS_PROJECT_DIR" in launcher
    assert "src" in launcher
    assert ".venv/bin/python" not in launcher


def test_environment_documentation_explains_editable_install() -> None:
    documentation = (ROOT / "docs" / "environment.md").read_text(encoding="utf-8")

    assert "pip install -e" in documentation
    assert "editable" in documentation
    assert "[dev]" in documentation


def test_launcher_runs_custom_inactive_environment_interactively_from_project_dir(
    tmp_path: Path,
) -> None:
    launcher, fake_bin, log = _launcher_fixture(tmp_path)
    _write_executable(
        fake_bin / "conda",
        """#!/bin/sh
{
  printf 'CALL cwd=<%s>\\n' "$PWD"
  for arg in "$@"; do printf 'arg=<%s>\\n' "$arg"; done
} >> "$SCHOLAROS_TEST_LOG"
""",
    )
    env = _launcher_env(fake_bin, log)
    env["CONDA_DEFAULT_ENV"] = "base"
    env["SCHOLAROS_CONDA_ENV"] = "my-research-env"

    result = subprocess.run(
        [str(launcher), "serve", "--port", "9000"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    calls = log.read_text(encoding="utf-8").split("CALL ")[1:]
    assert len(calls) == 2
    assert "arg=<--no-capture-output>" not in calls[0]
    assert "arg=<--no-capture-output>" in calls[1]
    assert "arg=<my-research-env>" in calls[1]
    assert f"cwd=<{launcher.parent}>" in calls[1]
    assert "arg=<serve>" in calls[1]
    assert "arg=<9000>" in calls[1]


def test_launcher_uses_current_python_when_requested_environment_is_active(
    tmp_path: Path,
) -> None:
    launcher, fake_bin, log = _launcher_fixture(tmp_path)
    _write_executable(fake_bin / "conda", "#!/bin/sh\nexit 99\n")
    _write_executable(
        fake_bin / "python",
        """#!/bin/sh
{
  printf 'CALL cwd=<%s>\\n' "$PWD"
  for arg in "$@"; do printf 'arg=<%s>\\n' "$arg"; done
} >> "$SCHOLAROS_TEST_LOG"
""",
    )
    env = _launcher_env(fake_bin, log)
    env["CONDA_DEFAULT_ENV"] = "my-research-env"
    env["SCHOLAROS_CONDA_ENV"] = "my-research-env"

    result = subprocess.run(
        [str(launcher), "search", "agent systems"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    calls = log.read_text(encoding="utf-8").split("CALL ")[1:]
    assert len(calls) == 2
    assert "arg=<-c>" in calls[0]
    assert f"cwd=<{launcher.parent}>" in calls[1]
    assert "arg=<-m>" in calls[1]
    assert "arg=<scholaros>" in calls[1]
    assert "arg=<agent systems>" in calls[1]


def test_launcher_failure_only_reports_install_commands_without_running_them(
    tmp_path: Path,
) -> None:
    launcher, fake_bin, log = _launcher_fixture(tmp_path)
    _write_executable(
        fake_bin / "conda",
        """#!/bin/sh
{
  printf 'CALL cwd=<%s>\\n' "$PWD"
  for arg in "$@"; do printf 'arg=<%s>\\n' "$arg"; done
} >> "$SCHOLAROS_TEST_LOG"
exit 1
""",
    )
    env = _launcher_env(fake_bin, log)
    env["CONDA_DEFAULT_ENV"] = "base"
    env["SCHOLAROS_CONDA_ENV"] = "my-research-env"

    result = subprocess.run(
        [str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "conda env create" in result.stderr
    assert 'conda run -n "my-research-env" python -m pip install' in result.stderr
    recorded = log.read_text(encoding="utf-8")
    assert recorded.count("CALL ") == 1
    assert "arg=<create>" not in recorded
    assert "arg=<install>" not in recorded
