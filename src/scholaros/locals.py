"""本机独立实验运行：不依赖论文项目，记录终端输出与指标。"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

METRIC_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
SAFE_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_WORKSPACE = 2 * 1024 * 1024 * 1024
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_METRIC_BYTES = 5 * 1024 * 1024
MAX_PREVIEW_BYTES = 128 * 1024
MAX_DETECT_FILES = 12
MAX_COMMAND = 2000
MAX_STREAM_BYTES = 256 * 1024
DEFAULT_CSV = "metrics.csv"
ENTRY_CANDIDATES = ("main.py", "run.py", "train.py", "run.sh")
CSV_KEYWORDS = ("metric", "result", "eval", "log")


@dataclass
class RunRecord:
    id: str
    status: str
    created_at: str
    command: str
    csv_path: str
    metric_columns: list[str]
    note: str
    file_hashes: dict[str, str]
    started_at: Any = None
    finished_at: Any = None
    exit_code: int | None = None
    error: str | None = None
    pid: int | None = None
    metrics: dict[str, Any] | None = None


class LocalRunService:
    """在 ScholarOS 数据目录之外维护独立的本机实验运行记录。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._deleting: set[str] = set()

    def list(self) -> list[dict[str, Any]]:
        records = []
        for directory in self.root.iterdir():
            path = directory / "run.json"
            if not path.is_file():
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if SAFE_ID.fullmatch(record.get("id", "")):
                records.append(record)
        records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return records

    def create(self, *, entries: list[tuple[str, bytes]], note: str) -> dict[str, Any]:
        return self.create_streamed(entries=[(name, BytesIO(content)) for name, content in entries], note=note)

    def create_streamed(self, *, entries: list[tuple[str, BinaryIO]], note: str) -> dict[str, Any]:
        if not entries:
            raise ValueError("工作区至少包含一个文件")
        paths = [self._safe_path(name) for name, _ in entries]
        if len(paths) != len({path.casefold() for path in paths}):
            raise ValueError("文件路径重复（包含仅大小写不同的路径）")

        run_id = uuid.uuid4().hex
        directory = self._run_dir(run_id)
        workspace = directory / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        hashes: dict[str, str] = {}
        total = 0
        try:
            for (_name, source), path in zip(entries, paths, strict=True):
                destination = workspace / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with destination.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_WORKSPACE:
                            raise ValueError("工作区不能超过 2 GiB")
                        target.write(chunk)
                        digest.update(chunk)
                hashes[path] = digest.hexdigest()
            record = RunRecord(
                id=run_id, status="created", created_at=_now(), command="", csv_path="",
                metric_columns=[], note=note[:1000], file_hashes=hashes,
            ).__dict__
            self._write_record(directory, record)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return record

    def link_project(self, run_id: str, project_id: str) -> None:
        """记录已确认的研究项目关联，不复制代码或原始数据。"""
        with self._lock:
            directory = self._require_record(run_id)
            record = self._read_record(directory)
            if record.get("project_id") and record["project_id"] != project_id:
                raise ValueError("该本机运行已关联研究项目")
            record["project_id"] = project_id
            self._write_record(directory, record)

    def workspace_files(self, run_id: str) -> list[str]:
        directory = self._require_record(run_id)
        record = self._read_record(directory)
        return sorted(record["file_hashes"])

    def detect_config(self, run_id: str) -> dict[str, Any]:
        """根据工作区内容建议运行命令和指标文件，不调用模型。"""
        files = self.workspace_files(run_id)
        command = ""
        for candidate in ENTRY_CANDIDATES:
            if candidate in files:
                command = f"bash {candidate}" if candidate.endswith(".sh") else f"python {candidate}"
                break
        csv_path, columns = self._detect_csv(run_id, files)
        return {"command": command, "csv_path": csv_path, "metric_columns": columns, "files": files}

    def validate_config(self, run_id: str, *, command: str, csv_path: str, metric_columns: list[str]) -> dict[str, Any]:
        if not command.strip():
            raise ValueError("请提供要在本机执行的命令")
        if len(command) > MAX_COMMAND:
            raise ValueError(f"命令不能超过 {MAX_COMMAND} 字符")
        csv_path = csv_path or DEFAULT_CSV
        if csv_path.startswith("../"):
            relative = csv_path[3:]
            if not relative.startswith(("outputs/", "results/", "output/")):
                raise ValueError("工作区外的指标文件仅支持 ../outputs/、../output/ 或 ../results/ 路径")
            self._safe_path(relative)
        else:
            self._safe_path(csv_path)
        if not csv_path.lower().endswith((".csv", ".txt")):
            raise ValueError("指标文件须为 CSV 或 TXT 相对路径")
        metric_columns = self._validate_metrics(metric_columns)
        return {"command": command.strip(), "csv_path": csv_path, "metric_columns": metric_columns}

    def start(self, run_id: str, *, command: str, csv_path: str, metric_columns: list[str], acknowledge_risk: bool) -> dict[str, Any]:
        with self._lock:
            if run_id in self._deleting:
                raise RuntimeError("该记录正在删除")
            return self._start(run_id, command=command, csv_path=csv_path,
                               metric_columns=metric_columns, acknowledge_risk=acknowledge_risk)

    def _start(self, run_id: str, *, command: str, csv_path: str, metric_columns: list[str], acknowledge_risk: bool) -> dict[str, Any]:
        if not acknowledge_risk:
            raise ValueError("请先确认宿主机执行风险")
        directory = self._require_record(run_id)
        record = self._read_record(directory)
        if run_id in self._processes:
            raise RuntimeError("该运行记录已经在本进程中执行")
        if record["status"] not in {"created", "failed"}:
            raise RuntimeError("该运行记录当前不能启动")
        workspace = directory / "workspace"
        if not workspace.is_dir():
            raise ValueError("没有可执行的工作区")
        config = self.validate_config(run_id, command=command, csv_path=csv_path, metric_columns=metric_columns)
        record.update(config)
        record["status"] = "running"
        record["started_at"] = _now()
        record["error"] = None
        self._write_record(directory, record)
        log_path = directory / "console.log"
        try:
            process = subprocess.Popen(
                record["command"], cwd=workspace, shell=True, start_new_session=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except OSError as exc:
            record["status"] = "failed"
            record["finished_at"] = _now()
            record["error"] = f"无法启动命令：{exc}"
            self._write_record(directory, record)
            raise RuntimeError(record["error"]) from exc
        self._processes[run_id] = process
        record["pid"] = process.pid
        self._write_record(directory, record)
        thread = threading.Thread(target=self._consume, args=(run_id, directory, process, log_path), daemon=True)
        self._threads[run_id] = thread
        thread.start()
        return record

    def _consume(self, run_id: str, directory: Path, process: subprocess.Popen[str], log_path: Path) -> None:
        try:
            assert process.stdout is not None
            with log_path.open("a", encoding="utf-8") as handle:
                for line in process.stdout:
                    _append_line(handle, line, log_path)
            exit_code = process.wait()
            process.stdout.close()
            with self._lock:
                try:
                    record = self._read_record(directory)
                except ValueError:
                    return
                record["status"] = "completed" if exit_code == 0 else "failed"
                record["exit_code"] = exit_code
                record["finished_at"] = _now()
                record["metrics"] = self._extract_metrics(directory)
                self._write_record(directory, record)
        finally:
            with self._lock:
                self._processes.pop(run_id, None)
                self._threads.pop(run_id, None)

    def stop(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            directory = self._require_record(run_id)
            if run_id in self._deleting:
                raise RuntimeError("该记录正在删除")
            process = self._processes.get(run_id)
            thread = self._threads.get(run_id)
        if process is not None:
            self._terminate(process)
            self._wait_for_output(process, thread)
        with self._lock:
            record = self._read_record(directory)
            if process is None and record.get("status") == "running":
                record["status"] = "failed"
                record["finished_at"] = _now()
                record["error"] = "服务重启后运行进程未被追踪，请确认本机原进程已结束"
                self._write_record(directory, record)
            return record

    def delete(self, run_id: str) -> None:
        """停止（如仍在运行）并删除本机运行记录所在目录。"""
        with self._lock:
            directory = self._require_record(run_id)
            if run_id in self._deleting:
                raise RuntimeError("该记录正在删除")
            self._deleting.add(run_id)
            process = self._processes.get(run_id)
            thread = self._threads.get(run_id)
        try:
            if process is not None:
                self._terminate(process)
                self._wait_for_output(process, thread)
            with self._lock:
                try:
                    shutil.rmtree(directory)
                except OSError as exc:
                    raise RuntimeError(f"无法删除运行记录：{exc}") from exc
        finally:
            with self._lock:
                self._deleting.discard(run_id)

    def get(self, run_id: str, *, after_bytes: int = 0, tail_bytes: int = 8192) -> dict[str, Any]:
        with self._lock:
            return self._get(run_id, after_bytes=after_bytes, tail_bytes=tail_bytes)

    def _get(self, run_id: str, *, after_bytes: int, tail_bytes: int) -> dict[str, Any]:
        directory = self._require_record(run_id)
        record = self._read_record(directory)
        if run_id not in self._processes and record.get("status") == "running":
            record["status"] = "failed"
            record["finished_at"] = record["finished_at"] or _now()
            record["error"] = "服务重启后运行进程未被追踪，请确认本机原进程已结束"
            self._write_record(directory, record)
        log_path = directory / "console.log"
        if log_path.exists():
            with log_path.open("rb") as handle:
                size = handle.seek(0, 2)
                if after_bytes > 0:
                    handle.seek(min(max(after_bytes, size - MAX_STREAM_BYTES), size))
                else:
                    handle.seek(max(0, size - min(tail_bytes, MAX_STREAM_BYTES)))
                payload = handle.read(MAX_STREAM_BYTES)
            record["log_size"] = size
            record["output"] = payload.decode("utf-8", errors="replace")
        else:
            record["log_size"] = 0
            record["output"] = ""
        record["metrics"] = self._extract_metrics(directory)
        record["files"] = sorted(record.pop("file_hashes", {}))
        return record

    def _detect_csv(self, run_id: str, files: list[str]) -> tuple[str, list[str]]:
        directory = self._require_record(run_id)
        metric_files = [path for path in files if path.lower().endswith((".csv", ".txt"))]
        if not metric_files:
            return DEFAULT_CSV, []
        root = [path for path in metric_files if "/" not in path]
        ranked = sorted(
            root or metric_files,
            key=lambda path: (0 if any(keyword in Path(path).name.lower() for keyword in CSV_KEYWORDS) else 1, path),
        )
        for path in ranked[:MAX_DETECT_FILES]:
            if not self._workspace_file(directory, path):
                continue
            columns = self._read_numeric_columns(directory / "workspace" / path)
            if columns:
                return path, columns
        return ranked[0], []

    @staticmethod
    def _read_numeric_columns(path: Path) -> list[str]:
        rows, _error = LocalRunService._read_metric_rows(path)
        if not rows:
            return []
        columns = []
        for name in rows[0]:
            if not name or not name.isidentifier():
                continue
            values = [row.get(name, "").strip() for row in rows]
            if not any(values):
                continue
            if all((value == "" or _is_float(value)) for value in values) and any(_is_float(value) for value in values):
                columns.append(name)
        return columns

    def _extract_metrics(self, directory: Path) -> dict[str, Any]:
        record = self._read_record(directory)
        csv_path = record["csv_path"]
        if not csv_path:
            return {"available": False, "path": "", "columns": []}
        if csv_path.startswith("../"):
            relative = csv_path[3:]
            if not relative.startswith(("outputs/", "output/", "results/")):
                return {"available": True, "error": "指标文件路径无效", "path": csv_path, "columns": []}
            base = directory
        else:
            relative = csv_path
            base = directory / "workspace"
        try:
            self._safe_path(relative)
        except ValueError:
            return {"available": True, "error": "指标文件路径无效", "path": csv_path, "columns": []}
        path = base / relative
        if not self._regular_file(base, relative):
            return {"available": False, "path": csv_path, "columns": []}
        rows, error = LocalRunService._read_metric_rows(path)
        if error:
            return {"available": True, "error": error, "path": csv_path, "columns": []}
        if not rows:
            return {"available": True, "rows": 0, "path": csv_path, "columns": []}
        columns = record["metric_columns"] or [name for name in rows[0] if name and name.isidentifier()]
        summary = []
        for column in columns:
            if column not in rows[0]:
                continue
            values = _numeric_values(rows, column)
            if not values:
                continue
            summary.append({
                "column": column, "count": len(values), "mean": sum(values) / len(values),
                "min": min(values), "max": max(values), "last": values[-1],
            })
        return {"available": True, "rows": len(rows), "path": csv_path, "columns": columns, "summary": summary}

    @staticmethod
    def _read_metric_rows(path: Path) -> tuple[list[dict[str, str]], str | None]:
        """按扩展名读取指标文件：CSV 读取表头，TXT 解析 key=value 行。"""
        try:
            with path.open("rb") as handle:
                data = handle.read(MAX_METRIC_BYTES + 1)
            if len(data) > MAX_METRIC_BYTES:
                return [], "指标文件超过 5 MiB，请缩小结果文件后重试"
            content = data.decode("utf-8-sig", errors="replace")
        except OSError as exc:
            return [], f"指标文件无法读取：{exc}"
        if path.suffix.lower() == ".txt":
            row = LocalRunService._parse_txt(content)
            return [row] if row else [], None
        try:
            return list(csv.DictReader(content.splitlines())), None
        except csv.Error as exc:
            return [], f"指标文件无法解析：{exc}"

    @staticmethod
    def _parse_txt(content: str) -> dict[str, str]:
        row: dict[str, str] = {}
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("#", "//")):
                continue
            match = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*[:=]\s*(.*)$", line)
            if not match:
                continue
            key, value = match.groups()
            if _is_float(value):
                row[key] = value
        return row

    @staticmethod
    def _regular_file(base: Path, name: str) -> bool:
        if base.is_symlink():
            return False
        parts = name.split("/")
        if any(base.joinpath(*parts[:index]).is_symlink() for index in range(1, len(parts) + 1)):
            return False
        return (base / name).is_file()

    @staticmethod
    def _workspace_file(directory: Path, name: str) -> bool:
        return LocalRunService._regular_file(directory / "workspace", name)

    def preview(self, run_id: str, name: str) -> dict[str, str]:
        directory = self._require_record(run_id)
        name = self._safe_path(name)
        if name not in self._read_record(directory)["file_hashes"]:
            raise ValueError("文件不属于上传的代码目录")
        if not self._workspace_file(directory, name):
            raise ValueError("文件不存在或已被改为符号链接")
        path = directory / "workspace" / name
        with path.open("rb") as handle:
            data = handle.read(MAX_PREVIEW_BYTES + 1)
        if len(data) > MAX_PREVIEW_BYTES:
            raise ValueError("文件超过 128 KiB，暂不支持预览")
        if b"\x00" in data:
            raise ValueError("二进制文件不支持预览")
        return {"path": name, "content": data.decode("utf-8", errors="replace")}

    @staticmethod
    def _safe_path(value: str) -> str:
        path = PurePosixPath(value)
        if (not value or len(value) > 300 or "\\" in value or ":" in value or "\x00" in value
                or any(char in value for char in "\n\r")
                or path.is_absolute() or len(path.parts) > 12
                or any(part in {".", ".."} or part.startswith(".") for part in value.split("/"))
                or path.as_posix() != value):
            raise ValueError("工作区文件路径无效或包含隐藏文件")
        return value

    @staticmethod
    def _validate_metrics(columns: list[str]) -> list[str]:
        if len(columns) > 64:
            raise ValueError("最多统计 64 个指标列")
        seen = set()
        result = []
        for column in columns:
            if not METRIC_NAME.fullmatch(column):
                raise ValueError("指标列名只能包含字母、数字和下划线")
            if column in seen:
                raise ValueError("指标列名不能重复")
            seen.add(column)
            result.append(column)
        return result

    def _require_record(self, run_id: str) -> Path:
        if not SAFE_ID.fullmatch(run_id):
            raise ValueError("运行记录 ID 格式无效")
        directory = self.root / run_id
        if directory.is_symlink() or not (directory / "run.json").is_file():
            raise ValueError("运行记录不存在")
        return directory

    def _terminate(self, process: subprocess.Popen[str]) -> None:
        """通知整个进程组退出，不只终止外层 shell。"""
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            if process.poll() is None:
                process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)

    def _wait_for_output(self, process: subprocess.Popen[str], thread: threading.Thread | None) -> None:
        if thread is None:
            return
        thread.join(timeout=5)
        if thread.is_alive():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("运行输出仍在收尾，请稍后重试")

    def _run_dir(self, run_id: str) -> Path:
        if not SAFE_ID.fullmatch(run_id):
            raise ValueError("运行记录 ID 格式无效")
        directory = self.root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _read_record(directory: Path) -> dict[str, Any]:
        path = directory / "run.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("运行记录文件缺失或损坏") from exc

    def _write_record(self, directory: Path, record: dict[str, Any]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._write_json(directory / "run.json", record)

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _append_line(handle: Any, line: str, path: Path) -> None:
    try:
        remaining = MAX_LOG_BYTES - path.stat().st_size
        if remaining > 0:
            handle.write(line.encode("utf-8")[:remaining].decode("utf-8", errors="ignore"))
            handle.flush()
    except OSError:
        pass


def _numeric_values(rows: list[dict[str, str]], column: str) -> list[float]:
    values = []
    for row in rows:
        raw = (row.get(column) or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _is_float(value: str) -> bool:
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _now() -> str:
    return datetime.now(UTC).isoformat()
