from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from os import PathLike
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from scholaros.config import Settings
from scholaros.domain import Event, Project, utc_now

if os.name == "nt":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

GENERATED_ARTIFACTS = frozenset(
    {
        "papers.json",
        "learning-plan.json",
        "contribution-options.json",
        "contribution-blueprint.json",
        "evidence.json",
        "research-design.json",
        "figure-story.json",
        "table-story.json",
        "paper-draft.md",
        "paper-draft.tex",
        "review.json",
        "paper.md",
        "final-review.json",
        "paper.docx",
        "paper.tex",
        "paper.pdf",
        "delivery-manifest.json",
        "delivery-package.zip",
    }
)


class ProjectBusyError(RuntimeError):
    """同一项目正在另一个工作流或进程中使用。"""


class ProjectStore:
    """SQLite 保存状态，文件系统保存可读制品。单进程写入由锁串行化。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_directories()
        self._lock = Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );
                CREATE INDEX IF NOT EXISTS events_project_seq
                ON events(project_id, seq);
                CREATE TABLE IF NOT EXISTS pending_deletions (
                    project_id TEXT PRIMARY KEY,
                    artifact_directory TEXT NOT NULL
                );
                """
            )
        self._retry_pending_deletions()

    def save_project(self, project: Project) -> None:
        project.updated_at = utc_now()
        payload = json.dumps(project.to_dict(), ensure_ascii=False)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, payload, status, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload=excluded.payload,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (project.id, payload, project.status.value, project.updated_at),
            )

    def get_project(self, project_id: str) -> Project | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return Project.from_dict(json.loads(row["payload"])) if row else None

    def list_projects(self, limit: int = 50) -> list[Project]:
        safe_limit = max(1, min(limit, 200))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM projects ORDER BY updated_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [Project.from_dict(json.loads(row["payload"])) for row in rows]

    def delete_project(self, project_id: str) -> bool:
        """删除项目状态、事件和制品；项目不存在时返回 False。"""
        self._validate_project_id(project_id)
        with self.project_lock(project_id):
            return self._delete_project_locked(project_id)

    @contextmanager
    def project_lock(self, project_id: str) -> Iterator[None]:
        """用系统文件锁阻止同一项目在 Windows 或 Unix 上被跨进程同时使用。"""
        self._validate_project_id(project_id)
        lock_directory = self.settings.home / "locks"
        lock_directory.mkdir(parents=True, exist_ok=True)
        lock_path = lock_directory / f"{project_id}.lock"
        handle = lock_path.open("a+b")
        acquired = False
        try:
            try:
                if os.name == "nt":
                    # Windows 锁定从当前位置开始的字节；空文件也能锁定首字节。
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                raise ProjectBusyError("项目正在另一个 ScholarOS 进程中运行") from exc
            acquired = True
            yield
        finally:
            try:
                if acquired:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _delete_project_locked(self, project_id: str) -> bool:
        directory = self.settings.artifacts_path / project_id
        tombstone = self.settings.artifacts_path / f".deleting-{project_id}-{uuid4().hex}"
        moved = directory.exists() or directory.is_symlink()

        with self._lock, self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if exists is None:
                pending = connection.execute(
                    "SELECT 1 FROM pending_deletions WHERE project_id = ?", (project_id,)
                ).fetchone()
                if pending is None:
                    return False
                retry_pending = True
            else:
                retry_pending = False
        if retry_pending:
            self._finish_pending_deletion(project_id)
            return True

        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if moved:
                    directory.rename(tombstone)
                connection.execute("DELETE FROM events WHERE project_id = ?", (project_id,))
                connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
                if moved:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO pending_deletions(project_id, artifact_directory)
                        VALUES (?, ?)
                        """,
                        (project_id, tombstone.name),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                if tombstone.exists() or tombstone.is_symlink():
                    tombstone.rename(directory)
                raise
        if moved:
            self._finish_pending_deletion(project_id)
        return True

    def _finish_pending_deletion(self, project_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT artifact_directory FROM pending_deletions WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return
        name = str(row["artifact_directory"])
        if Path(name).name != name or not name.startswith(f".deleting-{project_id}-"):
            raise ValueError("待删除制品目录记录无效")
        self._remove_tree(self.settings.artifacts_path / name)
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM pending_deletions WHERE project_id = ?", (project_id,))

    def _retry_pending_deletions(self) -> None:
        with self._connect() as connection:
            rows = connection.execute("SELECT project_id FROM pending_deletions").fetchall()
        for row in rows:
            project_id = str(row["project_id"])
            try:
                self._finish_pending_deletion(project_id)
            except (OSError, ValueError):
                logger.warning("项目 %s 的待删除制品将在下次启动时重试", project_id)

    @staticmethod
    def _remove_tree(path: PathLike[str] | Path) -> None:
        directory = Path(path)
        if directory.is_symlink():
            directory.unlink()
        elif directory.exists():
            shutil.rmtree(directory)

    def append_event(self, event: Event) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO events(project_id, type, payload, created_at) VALUES (?, ?, ?, ?)",
                (
                    event.project_id,
                    event.type,
                    json.dumps(event.payload, ensure_ascii=False),
                    event.created_at,
                ),
            )
            return int(cursor.lastrowid)

    def list_events(self, project_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT seq, type, payload, created_at FROM events
                WHERE project_id = ? AND seq > ? ORDER BY seq ASC
                """,
                (project_id, max(0, after)),
            ).fetchall()
        return [
            {
                "seq": row["seq"],
                "type": row["type"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def save_artifact(self, project_id: str, name: str, content: str | bytes) -> Path:
        self._validate_project_id(project_id)
        if Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError("制品名称必须是单个安全文件名")
        directory = self.settings.artifacts_path / project_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        temporary = directory / f".{name}.{uuid4().hex}.tmp"
        try:
            if isinstance(content, bytes):
                temporary.write_bytes(content)
            else:
                temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def snapshot_project(
        self,
        project: Project,
        reason: str,
        *,
        stage: str | None = None,
        kind: str = "change",
    ) -> str:
        """保留项目状态与生成制品；历史不包含密钥或配置环境。"""
        self._validate_project_id(project.id)
        root = self.settings.artifacts_path / project.id / "history"
        root.mkdir(parents=True, exist_ok=True)
        self._prune_stale_snapshots(root)
        revision = uuid4().hex
        temporary = root / f".{revision}.tmp"
        temporary.mkdir()
        try:
            hashes = {}
            for name in GENERATED_ARTIFACTS:
                source = self.artifact_path(project.id, name)
                if source is not None:
                    content = source.read_bytes()
                    (temporary / name).write_bytes(content)
                    hashes[name] = hashlib.sha256(content).hexdigest()
            manifest = {
                "revision": revision,
                "created_at": utc_now(),
                "reason": reason,
                "stage": stage,
                "kind": kind,
                "project": project.to_dict(),
                "artifacts": hashes,
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Windows 中杀毒扫描或同步软件可能短暂占用刚写完的目录。
            # 只重试目录被占用/拒绝访问，不掩盖真正的权限或磁盘错误。
            for attempt in range(5):
                try:
                    temporary.rename(root / revision)
                    break
                except PermissionError as exc:
                    if os.name != "nt" or exc.winerror not in {5, 32}:
                        raise
                    if attempt == 4:
                        raise PermissionError(
                            "历史快照目录持续被 Windows 拒绝访问；请检查文件占用、杀毒软件或目录权限。"
                            "已完成阶段的成果仍在，可从断点继续。"
                        ) from exc
                    time.sleep(0.1 * 2**attempt)
        except BaseException:
            for attempt in range(3):
                try:
                    shutil.rmtree(temporary)
                    break
                except OSError as cleanup_error:
                    if (os.name == "nt" and cleanup_error.winerror in {5, 32}
                            and attempt < 2):
                        time.sleep(0.1 * 2**attempt)
                        continue
                    logger.warning(
                        "项目 %s 的未完成历史快照未能清理：%s（%s）",
                        project.id, temporary, cleanup_error,
                    )
                    break
            raise
        return revision

    @staticmethod
    def _prune_stale_snapshots(root: Path) -> None:
        """仅清理本项目超过一天的临时快照，不触碰正式历史或活动目录。"""

        for path in root.iterdir():
            if not re.fullmatch(r"\.[a-f0-9]{32}\.tmp", path.name):
                continue
            try:
                if (path.is_symlink() or getattr(path, "is_junction", lambda: False)()
                        or path.resolve().parent != root.resolve()):
                    continue
                if path.is_dir() and time.time() - path.stat().st_mtime > 24 * 3600:
                    shutil.rmtree(path)
            except OSError as exc:
                logger.warning("过期历史快照临时目录暂无法清理：%s（%s）", path, exc)

    def list_history(self, project_id: str) -> list[dict[str, Any]]:
        self._validate_project_id(project_id)
        root = self.settings.artifacts_path / project_id / "history"
        entries = []
        if root.is_dir():
            for path in root.glob("*/manifest.json"):
                if not re.fullmatch(r"[a-f0-9]{32}", path.parent.name):
                    continue
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    logger.warning("项目 %s 的历史清单存在损坏条目，已跳过：%s", project_id, path.parent.name)
                    continue
                project = value.get("project", {})
                entries.append(
                    {
                        "revision": value["revision"],
                        "created_at": value["created_at"],
                        "reason": value["reason"],
                        "stage": value.get("stage"),
                        "kind": value.get("kind", "change"),
                        "status": project.get("status"),
                        "title": project.get("title") or project.get("idea"),
                        "artifacts": value.get("artifacts", {}),
                    }
                )
        return sorted(entries, key=lambda value: value["created_at"], reverse=True)

    def history_manifest(self, project_id: str, revision: str) -> dict[str, Any] | None:
        path = self.history_artifact_path(project_id, revision, "manifest.json")
        if path is None:
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def history_artifact_path(self, project_id: str, revision: str, name: str) -> Path | None:
        if not self._is_project_id(project_id) or not re.fullmatch(r"[a-f0-9]{32}", revision):
            return None
        if name not in GENERATED_ARTIFACTS | {"manifest.json"}:
            return None
        path = self.settings.artifacts_path / project_id / "history" / revision / name
        return path if path.is_file() else None

    def artifact_path(self, project_id: str, name: str) -> Path | None:
        if not self._is_project_id(project_id):
            return None
        if Path(name).name != name:
            return None
        path = self.settings.artifacts_path / project_id / name
        return path if path.is_file() else None

    def list_artifacts(self, project_id: str) -> list[str]:
        if not self._is_project_id(project_id):
            return []
        directory = self.settings.artifacts_path / project_id
        if not directory.exists():
            return []
        return sorted(
            path.name for path in directory.iterdir()
            if path.is_file() and not path.name.startswith(".")
        )

    def clear_generated_artifacts(
        self, project_id: str, names: frozenset[str] = GENERATED_ARTIFACTS
    ) -> None:
        """重新运行前移除上一轮产物，同时保留用户上传的 source-* 资料。"""

        self._validate_project_id(project_id)
        directory = self.settings.artifacts_path / project_id
        if directory.is_symlink():
            raise ValueError("项目制品目录不能是符号链接")
        if not directory.is_dir():
            return
        paths = [
            path
            for name in GENERATED_ARTIFACTS & names
            if (path := directory / name).is_file() or path.is_symlink()
        ]
        if not paths:
            return
        quarantine = directory / f".stale-{uuid4().hex}"
        quarantine.mkdir()
        moved: list[Path] = []
        try:
            for path in paths:
                path.rename(quarantine / path.name)
                moved.append(path)
        except Exception:
            for path in reversed(moved):
                (quarantine / path.name).rename(path)
            quarantine.rmdir()
            raise
        try:
            self._remove_tree(quarantine)
        except OSError:
            logger.warning("项目 %s 的旧版生成制品已隔离，将在后续人工清理", project_id)

    @staticmethod
    def _is_project_id(value: str) -> bool:
        return bool(re.fullmatch(r"[a-f0-9]{12}", value))

    @classmethod
    def _validate_project_id(cls, value: str) -> None:
        if not cls._is_project_id(value):
            raise ValueError("项目 ID 格式无效")
