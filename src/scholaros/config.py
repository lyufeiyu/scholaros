from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    """应用配置。密钥只在调用时从环境变量读取，不写入项目数据库。"""

    home: Path
    model: str
    api_base: str
    api_key_env: str
    contact_email: str | None
    semantic_scholar_api_key: str | None
    ieee_xplore_api_key: str | None
    model_timeout: float = 300.0
    model_thinking: str = "auto"
    request_timeout: float = 25.0

    @classmethod
    def from_env(cls, cwd: Path | None = None) -> Settings:
        base = _project_base(cwd)
        if cwd is not None or os.getenv("SCHOLAROS_PROJECT_DIR") or _looks_like_project_root(base):
            load_dotenv(base / ".env", override=False)
        home_value = os.getenv("SCHOLAROS_HOME", ".scholaros")
        home = Path(home_value).expanduser()
        if not home.is_absolute():
            home = base / home
        model_thinking = os.getenv("SCHOLAROS_MODEL_THINKING", "auto").strip().lower()
        if model_thinking not in {"auto", "enabled", "disabled"}:
            raise ValueError(
                "SCHOLAROS_MODEL_THINKING 只能是 auto、enabled 或 disabled"
            )
        return cls(
            home=home.resolve(),
            model=os.getenv("SCHOLAROS_MODEL", "your-model-name"),
            api_base=os.getenv("SCHOLAROS_API_BASE", "https://api.openai.com/v1").rstrip("/"),
            api_key_env=os.getenv("SCHOLAROS_API_KEY_ENV", "OPENAI_API_KEY"),
            contact_email=os.getenv("SCHOLAROS_CONTACT_EMAIL") or None,
            semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None,
            ieee_xplore_api_key=os.getenv("IEEE_XPLORE_API_KEY") or None,
            model_timeout=_float_env(
                "SCHOLAROS_MODEL_TIMEOUT_SECONDS", default=300.0, minimum=30.0, maximum=1800.0
            ),
            model_thinking=model_thinking,
            request_timeout=_float_env(
                "SCHOLAROS_SOURCE_TIMEOUT_SECONDS", default=25.0, minimum=5.0, maximum=300.0
            ),
        )

    @property
    def api_key(self) -> str | None:
        return os.getenv(self.api_key_env) or None

    @property
    def database_path(self) -> Path:
        return self.home / "scholaros.db"

    @property
    def artifacts_path(self) -> Path:
        return self.home / "artifacts"

    def ensure_directories(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.artifacts_path.mkdir(parents=True, exist_ok=True)


def _float_env(name: str, *, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须在 {minimum:g}—{maximum:g} 秒之间")
    return value


def _project_base(cwd: Path | None) -> Path:
    """确定配置根目录，避免从其他目录启动时漏读项目根目录的 .env。"""

    if cwd is not None:
        return cwd.expanduser().resolve()

    configured = os.getenv("SCHOLAROS_PROJECT_DIR")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_dir():
            return candidate.resolve()

    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if _looks_like_project_root(candidate):
            return candidate

    # editable 安装时 __file__ 位于 <project>/src/scholaros；普通 wheel
    # 则回退到当前目录，不会误读环境中其他项目的配置。
    editable_root = Path(__file__).resolve().parents[2]
    if _looks_like_project_root(editable_root):
        return editable_root
    return current


def _looks_like_project_root(path: Path) -> bool:
    """只把包含 ScholarOS 源码布局的目录识别为项目根，避免串读邻近项目配置。"""

    return (path / "src" / "scholaros").is_dir() and (
        (path / ".env").is_file() or (path / "pyproject.toml").is_file()
    )
