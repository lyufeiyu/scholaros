from __future__ import annotations

from pathlib import Path

import pytest

from scholaros.config import Settings


def test_model_and_source_timeouts_can_be_configured_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCHOLAROS_MODEL_TIMEOUT_SECONDS", "450")
    monkeypatch.setenv("SCHOLAROS_SOURCE_TIMEOUT_SECONDS", "18")
    monkeypatch.setenv("SCHOLAROS_MODEL_THINKING", "enabled")

    settings = Settings.from_env(tmp_path)

    assert settings.model_timeout == 450
    assert settings.request_timeout == 18
    assert settings.model_thinking == "enabled"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("SCHOLAROS_MODEL_TIMEOUT_SECONDS", "fast", "必须是数字"),
        ("SCHOLAROS_MODEL_TIMEOUT_SECONDS", "20", "30—1800 秒"),
        ("SCHOLAROS_SOURCE_TIMEOUT_SECONDS", "301", "5—300 秒"),
    ],
)
def test_invalid_timeout_configuration_fails_clearly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        Settings.from_env(tmp_path)


def test_invalid_thinking_mode_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCHOLAROS_MODEL_THINKING", "sometimes")

    with pytest.raises(ValueError, match="auto、enabled 或 disabled"):
        Settings.from_env(tmp_path)


def test_project_dir_environment_locates_dotenv_when_started_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "SCHOLAROS_MODEL=project-model\nSCHOLAROS_API_BASE=https://project.invalid\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SCHOLAROS_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("SCHOLAROS_MODEL", raising=False)
    monkeypatch.delenv("SCHOLAROS_API_BASE", raising=False)

    settings = Settings.from_env()

    assert settings.model == "project-model"
    assert settings.api_base == "https://project.invalid"


def test_unrelated_parent_project_is_not_used_for_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='other-project'\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SCHOLAROS_MODEL=other-model\n", encoding="utf-8")
    monkeypatch.delenv("SCHOLAROS_PROJECT_DIR", raising=False)
    monkeypatch.delenv("SCHOLAROS_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env()

    assert settings.model != "other-model"
