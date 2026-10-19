"""项目级计算实验：固定输入、受限容器执行、保留原始试验记录。"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from scholaros.storage import ProjectBusyError, ProjectStore
from scholaros.workflow import ResearchWorkflow

IMAGE = "python:3.11-slim"
MAX_UPLOAD = 20 * 1024 * 1024
MAX_FOLDER = 2 * 1024 * 1024 * 1024
INPUT_SUFFIXES = {"dataset": ".csv", "baseline": ".py", "candidate": ".py"}
METRIC_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class ContainerCleanupError(RuntimeError):
    """超时容器未确认停止；阻止继续运行和覆盖活动状态。"""


class ExperimentProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis: str = Field(min_length=8, max_length=2000)
    intervention: str = Field(min_length=4, max_length=1000)
    metric: str = Field(min_length=1, max_length=64)
    direction: str = Field(pattern="^(maximize|minimize)$")
    seeds: list[int] = Field(min_length=1, max_length=3)
    variants: list[dict[str, int | float | str | bool]] = Field(min_length=1, max_length=4)
    timeout_seconds: int = Field(default=60, ge=10, le=300)
    acknowledge_code_risk: bool
    mode: Literal["single", "folder"] = "single"
    folder_id: str | None = Field(default=None, pattern="^[0-9a-f]{32}$")
    main_file: str | None = None
    baseline_file: str | None = None
    baseline_command: str | None = Field(default=None, max_length=500)
    dataset_file: str | None = None
    command: str | None = Field(default=None, max_length=500)
    optimization_proposal_id: str | None = Field(default=None, pattern="^[0-9a-f]{32}$")

    @field_validator("metric")
    @classmethod
    def valid_metric(cls, value: str) -> str:
        if not METRIC_NAME.fullmatch(value):
            raise ValueError("指标名称只能包含字母、数字和下划线，且以字母开头")
        return value

    @field_validator("seeds")
    @classmethod
    def valid_seeds(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value) or any(seed < 0 or seed > 2**31 - 1 for seed in value):
            raise ValueError("随机种子必须互不相同，范围为 0 至 2^31-1")
        return value

    @field_validator("variants")
    @classmethod
    def valid_variants(cls, value: list[dict[str, int | float | str | bool]]) -> list[dict[str, int | float | str | bool]]:
        for variant in value:
            if len(variant) > 12 or any(not METRIC_NAME.fullmatch(key) for key in variant):
                raise ValueError("每组最多 12 个有效命名的参数")
            for item in variant.values():
                if isinstance(item, float) and not math.isfinite(item):
                    raise ValueError("参数不能是非有限数值")
                if isinstance(item, str) and len(item) > 200:
                    raise ValueError("参数文字不能超过 200 字符")
        if len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ValueError("参数候选不能重复")
        return value


class ExperimentService:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def _root(self, project_id: str) -> Path:
        self.store._validate_project_id(project_id)
        if self.store.get_project(project_id) is None:
            raise KeyError(project_id)
        return self.store.settings.artifacts_path / project_id / "experiments"

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temp.write_text(
                json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
            )
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _tree_hash(files: dict[str, str]) -> str:
        return hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()

    def status(self, project_id: str) -> dict[str, Any]:
        root = self._root(project_id)
        inputs = {}
        for role, suffix in INPUT_SUFFIXES.items():
            path = root / "inputs" / f"{role}{suffix}"
            if path.is_file():
                inputs[role] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        runs = []
        directory = root / "runs"
        if directory.is_dir():
            for path in sorted(
                directory.glob("*/report.json"), key=lambda item: item.stat().st_mtime, reverse=True
            )[:15]:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
        current = root / "current.json"
        active = json.loads(current.read_text(encoding="utf-8")) if current.is_file() else None
        marker = root / "adopted.json"
        marker_value = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else None
        adopted = marker_value["run_id"] if marker_value and marker_value["status"] == "adopted" else None
        manifest = root / "folder.json"
        folder = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else None
        proposal_path = root / "optimization.json"
        proposal = json.loads(proposal_path.read_text(encoding="utf-8")) if proposal_path.is_file() else None
        return {"inputs": inputs, "folder": folder, "optimization": proposal,
                "active": active, "runs": runs, "adopted": adopted}

    @staticmethod
    def _safe_path(value: str) -> str:
        path = PurePosixPath(value)
        if (not value or len(value) > 300 or "\\" in value or ":" in value
                or any(char in value for char in "\n\r\x00")
                or path.is_absolute() or len(path.parts) > 12
                or any(part in {".", ".."} or part.startswith(".") for part in value.split("/"))
                or path.as_posix() != value):
            raise ValueError("文件夹路径无效或包含隐藏文件")
        return value

    def upload_folder(self, project_id: str, entries: list[tuple[str, bytes]]) -> dict[str, Any]:
        return self.upload_folder_streamed(project_id, [(name, BytesIO(content)) for name, content in entries])

    def upload_folder_streamed(self, project_id: str, entries: list[tuple[str, BinaryIO]]) -> dict[str, Any]:
        with self.store.project_lock(project_id):
            root = self._root(project_id)
            if (root / "current.json").exists():
                raise RuntimeError("实验运行中不能更换项目文件夹")
            if not entries:
                raise ValueError("项目文件夹至少包含一个文件")
            paths = [self._safe_path(name) for name, _ in entries]
            if len({path.casefold() for path in paths}) != len(paths) or not any(path.endswith(".py") for path in paths):
                raise ValueError("文件路径重复或缺少 Python 主文件")
            path_set = set(paths)
            if any("/".join(parts[:index]) in path_set
                   for path in paths for parts in [path.split("/")]
                   for index in range(1, len(parts))):
                raise ValueError("文件路径与子目录冲突")
            root.mkdir(parents=True, mode=0o700, exist_ok=True)
            root.chmod(0o700)
            folder_id = uuid4().hex
            directory = root / "folders" / folder_id
            files = {}
            total = 0
            try:
                for (_name, source), path in zip(entries, paths, strict=True):
                    destination = directory / path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with destination.open("wb") as target:
                        while chunk := source.read(1024 * 1024):
                            total += len(chunk)
                            if total > MAX_FOLDER:
                                raise ValueError("项目文件夹不能超过 2 GiB")
                            target.write(chunk)
                            digest.update(chunk)
                    files[path] = digest.hexdigest()
                manifest = {"id": folder_id, "files": files}
                old = root / "folder.json"
                old_id = json.loads(old.read_text(encoding="utf-8"))["id"] if old.is_file() else None
                self._write_json(old, manifest)
                (root / "optimization.json").unlink(missing_ok=True)
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            if old_id:
                shutil.rmtree(root / "folders" / old_id, ignore_errors=True)
            return manifest

    def folder_file(self, project_id: str, path: str, *, suffix: str = ".py") -> Path:
        root = self._root(project_id)
        manifest_path = root / "folder.json"
        if not manifest_path.is_file():
            raise ValueError("请先上传项目文件夹")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        path = self._safe_path(path)
        if path not in manifest["files"] or (suffix and not path.endswith(suffix)):
            raise ValueError("所选文件不在已上传的项目文件夹内")
        return root / "folders" / manifest["id"] / path

    def save_optimization(
        self, project_id: str, folder_id: str, main_file: str,
        original_hashes: dict[str, str], edits: list[dict[str, str]], reason: str, goal: str,
    ) -> dict[str, Any]:
        if not edits:
            raise ValueError("候选修改至少需要一个 Python 文件")
        with self.store.project_lock(project_id):
            root = self._root(project_id)
            manifest = json.loads((root / "folder.json").read_text(encoding="utf-8"))
            if manifest["id"] != folder_id:
                raise ValueError("项目文件夹已更换，请重新生成候选代码")
            self.folder_file(project_id, main_file)
            selected_paths = set(original_hashes)
            if not selected_paths:
                raise ValueError("请选择至少一个可修改的 Python 文件")
            total_candidate_bytes = 0
            changes = []
            differences = []
            seen = set()
            for edit in edits:
                if not isinstance(edit, dict) or not isinstance(edit.get("path"), str) or not isinstance(edit.get("code"), str):
                    raise ValueError("候选修改须包含文件路径与完整 Python 源码")
                path = self._safe_path(edit["path"])
                code = edit["code"]
                if path not in selected_paths or path in seen:
                    raise ValueError("模型修改了未授权文件或重复修改同一文件")
                seen.add(path)
                original = self.folder_file(project_id, path).read_bytes()
                before_hash = hashlib.sha256(original).hexdigest()
                if before_hash != original_hashes[path]:
                    raise ValueError("待修改文件已更换，请重新生成候选代码")
                if not code or len(code.encode("utf-8")) > 48 * 1024:
                    raise ValueError(f"候选文件 {path} 为空或超过 48 KiB")
                total_candidate_bytes += len(code.encode("utf-8"))
                if total_candidate_bytes > 256 * 1024:
                    raise ValueError("候选代码合计不能超过 256 KiB")
                try:
                    ast.parse(code, filename=path, feature_version=(3, 11))
                except SyntaxError as exc:
                    raise ValueError(f"候选文件 {path} 语法错误：第 {exc.lineno} 行") from exc
                diff = "".join(difflib.unified_diff(
                    original.decode("utf-8", errors="replace").splitlines(keepends=True),
                    code.splitlines(keepends=True), fromfile=f"原版/{path}", tofile=f"候选/{path}",
                ))
                if diff:
                    changes.append({"path": path, "original_sha256": before_hash,
                                    "candidate_sha256": hashlib.sha256(code.encode()).hexdigest(), "code": code})
                    differences.append(diff)
            if not changes:
                raise ValueError("模型未修改所选 Python 文件")
            proposal = {
                "id": uuid4().hex, "folder_id": folder_id, "main_file": main_file,
                "edits": changes, "reason": reason[:1000], "goal": goal[:1000],
                "diff": "\n".join(differences),
            }
            self._write_json(root / "optimization.json", proposal)
            return proposal

    @staticmethod
    def command_argv(command: str, main_file: str) -> list[str]:
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            raise ValueError("运行命令引号不完整") from exc
        if (len(tokens) < 2 or len(tokens) > 20 or tokens[0] not in {"python", "python3"}
                or any("\n" in token or "\r" in token for token in tokens)
                or any(token in {";", "|", "||", "&", "&&", ">", ">>", "<", "<<"} for token in tokens[2:])):
            raise ValueError("只支持 Python 主文件或对应模块，不执行 Shell 命令")
        if tokens[1] == "-m":
            module = main_file.removesuffix(".py").replace("/", ".")
            if module.endswith(".__main__"):
                module = module.removesuffix(".__main__")
            if len(tokens) < 3 or tokens[2] != module:
                raise ValueError("模块命令必须对应所选主文件")
            return ["python", "-m", module, *tokens[3:]]
        if tokens[1] not in {main_file, f"./{main_file}", f"/input/{main_file}"}:
            raise ValueError("命令必须执行所选主文件")
        return ["python", f"/input/{main_file}", *tokens[2:]]

    def upload(self, project_id: str, role: str, content: bytes) -> dict[str, str]:
        if role not in INPUT_SUFFIXES:
            raise ValueError("实验材料角色只能是 dataset、baseline 或 candidate")
        if not content or len(content) > MAX_UPLOAD:
            raise ValueError("实验材料必须非空且不超过 20 MiB")
        with self.store.project_lock(project_id):
            return self._upload_locked(project_id, role, content)

    def _upload_locked(self, project_id: str, role: str, content: bytes) -> dict[str, str]:
        root = self._root(project_id)
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        root.chmod(0o700)
        if (root / "current.json").exists():
            raise RuntimeError("实验运行中不能更换输入")
        if role in {"baseline", "candidate"}:
            content.decode("utf-8")
        path = root / "inputs" / f"{role}{INPUT_SUFFIXES[role]}"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temp.write_bytes(content)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        return {"role": role, "sha256": hashlib.sha256(content).hexdigest()}

    def prepare(self, project_id: str, protocol: ExperimentProtocol) -> str:
        with self.store.project_lock(project_id):
            return self._prepare_locked(project_id, protocol)

    def _prepare_locked(self, project_id: str, protocol: ExperimentProtocol) -> str:
        if not protocol.acknowledge_code_risk:
            raise ValueError("请先确认上传脚本的执行风险")
        root = self._root(project_id)
        if shutil.which("docker") is None:
            raise RuntimeError("未找到 Docker；实验不会在宿主机上执行")
        inspected = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False,
        )
        if inspected.returncode != 0:
            raise RuntimeError(f"缺少本地容器镜像 {IMAGE}；请先自行准备镜像")
        image_id = inspected.stdout.decode("utf-8").strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RuntimeError("无法核验本地容器镜像标识")
        if (root / "current.json").exists():
            raise RuntimeError("该项目已有正在运行的实验")
        if protocol.mode == "folder":
            manifest = json.loads((root / "folder.json").read_text(encoding="utf-8")) if (root / "folder.json").is_file() else None
            if not manifest or manifest["id"] != protocol.folder_id:
                raise ValueError("项目文件夹已更换，请刷新后重新确认")
            if not protocol.main_file or not protocol.command:
                raise ValueError("请选择主文件并确认 Python 运行命令")
            self.folder_file(project_id, protocol.main_file)
            if protocol.baseline_file:
                self.folder_file(project_id, protocol.baseline_file)
                self.command_argv(
                    protocol.baseline_command or f"python {shlex.quote(protocol.baseline_file)}",
                    protocol.baseline_file,
                )
            if protocol.dataset_file:
                self.folder_file(project_id, protocol.dataset_file, suffix="")
            self.command_argv(protocol.command, protocol.main_file)
            proposal = None
            if protocol.optimization_proposal_id:
                if protocol.baseline_file:
                    raise ValueError("算法优化模式自动使用原版主文件作为基线")
                proposal_path = root / "optimization.json"
                proposal = json.loads(proposal_path.read_text(encoding="utf-8")) if proposal_path.is_file() else None
                if (not proposal or proposal["id"] != protocol.optimization_proposal_id
                        or proposal["folder_id"] != protocol.folder_id
                        or proposal["main_file"] != protocol.main_file):
                    raise ValueError("候选代码与当前项目目录不一致，请重新生成")
                for edit in proposal["edits"]:
                    original = self.folder_file(project_id, edit["path"])
                    if (hashlib.sha256(original.read_bytes()).hexdigest() != edit["original_sha256"]
                            or hashlib.sha256(edit["code"].encode()).hexdigest() != edit["candidate_sha256"]):
                        raise ValueError("候选算法文件与当前目录不一致，请重新生成")
        elif protocol.optimization_proposal_id:
            raise ValueError("算法优化仅支持项目文件夹模式")
        else:
            inputs = root / "inputs"
            if any(not (inputs / f"{role}{suffix}").is_file() for role, suffix in INPUT_SUFFIXES.items()):
                raise ValueError("请先上传 CSV 数据集、基线脚本和候选脚本")
        run_id = uuid4().hex
        run_dir = root / "runs" / run_id
        run_dir.mkdir(parents=True)
        inputs = run_dir / "inputs"
        inputs.mkdir()
        if protocol.mode == "folder":
            manifest = json.loads((root / "folder.json").read_text(encoding="utf-8"))
            shutil.copytree(root / "folders" / manifest["id"], inputs / "project")
            input_hashes = {
                path: hashlib.sha256((inputs / "project" / path).read_bytes()).hexdigest()
                for path in manifest["files"]
            }
            if proposal:
                shutil.copytree(inputs / "project", inputs / "optimized")
                optimized_hashes = dict(input_hashes)
                edits = []
                for edit in proposal["edits"]:
                    path = edit["path"]
                    if input_hashes[path] != edit["original_sha256"]:
                        raise ValueError("候选算法文件的原始哈希不匹配")
                    candidate = inputs / "optimized" / path
                    candidate.write_text(edit["code"], encoding="utf-8")
                    optimized_hashes[path] = hashlib.sha256(candidate.read_bytes()).hexdigest()
                    edits.append({"path": path, "original_sha256": input_hashes[path],
                                  "candidate_sha256": optimized_hashes[path]})
                optimized = {
                    "proposal_id": proposal["id"], "main_file": protocol.main_file,
                    "edits": edits,
                    "original_tree_sha256": self._tree_hash(input_hashes),
                    "candidate_tree_sha256": self._tree_hash(optimized_hashes),
                }
        else:
            for role, suffix in INPUT_SUFFIXES.items():
                shutil.copyfile(root / "inputs" / f"{role}{suffix}", inputs / f"{role}{suffix}")
            input_hashes = {
                role: hashlib.sha256((inputs / f"{role}{suffix}").read_bytes()).hexdigest()
                for role, suffix in INPUT_SUFFIXES.items()
            }
        snapshot = {
            "id": run_id,
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "image_id": image_id,
            "protocol": protocol.model_dump(exclude={"acknowledge_code_risk"}),
            "inputs": input_hashes,
            "trials": [],
        }
        if protocol.mode == "folder" and proposal:
            snapshot["optimized"] = optimized
        self._write_json(run_dir / "report.json", snapshot)
        self._write_json(root / "current.json", {"id": run_id, "status": "running"})
        return run_id

    def execute(self, project_id: str, run_id: str) -> None:
        with self.store.project_lock(project_id):
            self._execute_locked(project_id, run_id)

    def _execute_locked(self, project_id: str, run_id: str) -> None:
        root = self._root(project_id)
        report_path = root / "runs" / run_id / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        protocol = ExperimentProtocol(**report["protocol"], acknowledge_code_risk=True)
        try:
            roles = (("baseline", [{}]), ("candidate", protocol.variants))
            if protocol.optimization_proposal_id:
                roles = (("baseline", protocol.variants), ("candidate", protocol.variants))
            if protocol.mode == "folder" and not (protocol.baseline_file or protocol.optimization_proposal_id):
                roles = (("candidate", protocol.variants),)
            for role, variants in roles:
                for variant in variants:
                    for seed in protocol.seeds:
                        trial_id = uuid4().hex
                        report["active_trial"] = trial_id
                        self._write_json(report_path, report)
                        source_hash = None
                        if protocol.optimization_proposal_id:
                            key = "original_tree_sha256" if role == "baseline" else "candidate_tree_sha256"
                            source_hash = report["optimized"][key]
                        trial = self._trial(
                            root / "runs" / run_id, role, variant, seed, protocol,
                            report["image_id"], trial_id, source_hash=source_hash,
                        )
                        report["trials"].append(trial)
                        report.pop("active_trial", None)
                        self._write_json(report_path, report)
            valid = [item for item in report["trials"] if item["status"] == "completed"]
            expected = len(protocol.seeds) * sum(len(variants) for _, variants in roles)
            report["status"] = "completed" if len(valid) == expected else "partial"
            report["summary"] = self._summarize(report["trials"])
            if report["status"] == "completed":
                report["comparison"] = self._comparison(report["summary"], protocol)
        except Exception as exc:
            report["status"] = "cleanup_required" if isinstance(exc, ContainerCleanupError) else "failed"
            report["error"] = str(exc)[:500]
        finally:
            report["finished_at"] = datetime.now(UTC).isoformat()
            self._write_json(report_path, report)
            current = root / "current.json"
            if (report["status"] != "cleanup_required" and current.is_file()
                    and json.loads(current.read_text(encoding="utf-8")).get("id") == run_id):
                current.unlink()
            elif report["status"] == "cleanup_required":
                self._write_json(current, {"id": run_id, "status": "cleanup_required"})

    def recover_interrupted(self, project_id: str) -> None:
        try:
            with self.store.project_lock(project_id):
                self._recover_interrupted_locked(project_id)
        except ProjectBusyError:
            # 另一进程仍在执行，不应误判为中断。
            return

    def _recover_interrupted_locked(self, project_id: str) -> None:
        root = self._root(project_id)
        current = root / "current.json"
        if not current.is_file():
            return
        run_id = json.loads(current.read_text(encoding="utf-8"))["id"]
        report_path = root / "runs" / run_id / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        active_trial = report.get("active_trial")
        report.pop("warning", None)
        if active_trial and re.fullmatch(r"[0-9a-f]{32}", active_trial) and shutil.which("docker"):
            if not self._remove_container(f"scholaros-{active_trial}"):
                report["warning"] = "请检查中断的 Docker 容器是否仍在运行"
        elif active_trial:
            report["warning"] = "Docker 不可用，无法确认中断容器是否停止"
        if report.get("warning"):
            report["status"] = "cleanup_required"
            self._write_json(report_path, report)
            self._write_json(current, {"id": run_id, "status": "cleanup_required"})
            return
        report.pop("active_trial", None)
        report["status"] = "interrupted"
        report["finished_at"] = datetime.now(UTC).isoformat()
        self._write_json(report_path, report)
        current.unlink()

    @staticmethod
    def _remove_container(name: str) -> bool:
        try:
            removed = subprocess.run(
                ["docker", "rm", "-f", name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
            )
            if removed.returncode == 0:
                return True
            listing = subprocess.run(
                ["docker", "ps", "-aq", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False,
            )
            return listing.returncode == 0 and not listing.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return False

    def adopt(self, project_id: str, run_id: str, workflow: ResearchWorkflow) -> dict[str, Any]:
        with self.store.project_lock(project_id):
            return self._adopt_locked(project_id, run_id, workflow)

    def _adopt_locked(
        self, project_id: str, run_id: str, workflow: ResearchWorkflow
    ) -> dict[str, Any]:
        root = self._root(project_id)
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("运行 ID 格式无效")
        report_path = root / "runs" / run_id / "report.json"
        if not report_path.is_file():
            raise ValueError("实验报告不存在")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report["status"] != "completed":
            raise ValueError("只有所有试验完成的报告才能作为真实结果导入")
        protocol = ExperimentProtocol(**report["protocol"], acknowledge_code_risk=True)
        baseline_variants = protocol.variants if protocol.optimization_proposal_id else [{}]
        expected = [("baseline", variant, seed) for variant in baseline_variants for seed in protocol.seeds]
        if protocol.mode == "folder" and not (protocol.baseline_file or protocol.optimization_proposal_id):
            expected = []
        expected += [
            ("candidate", variant, seed) for variant in protocol.variants for seed in protocol.seeds
        ]
        trials = report["trials"]
        if (len(trials) != len(expected) or
                [(item["role"], item["parameters"], item["seed"]) for item in trials] != expected or
                any(item["status"] != "completed" for item in trials) or
                len({item["id"] for item in trials}) != len(expected) or
                any(not re.fullmatch(r"[0-9a-f]{32}", item["id"]) for item in trials)):
            raise ValueError("试验明细不完整或与实验协议不符")
        inputs = root / "runs" / run_id / "inputs"
        if protocol.mode == "folder":
            folder = inputs / "project"
            actual_paths = {
                path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()
            }
            if set(report["inputs"]) != actual_paths:
                raise ValueError("实验输入清单与快照不符")
            self.command_argv(protocol.command or "", protocol.main_file or "")
            for path, digest in report["inputs"].items():
                if hashlib.sha256((folder / self._safe_path(path)).read_bytes()).hexdigest() != digest:
                    raise ValueError("实验输入快照与记录的哈希不符")
            if protocol.optimization_proposal_id:
                optimized = inputs / "optimized"
                metadata = report.get("optimized") or {}
                paths = {path.relative_to(optimized).as_posix() for path in optimized.rglob("*") if path.is_file()}
                edits = metadata.get("edits") or []
                edit_paths = {edit["path"] for edit in edits}
                if (paths != actual_paths or metadata.get("proposal_id") != protocol.optimization_proposal_id
                        or metadata.get("main_file") != protocol.main_file
                        or not edits or len(edit_paths) != len(edits)
                        or not edit_paths.issubset(actual_paths)
                        or metadata.get("original_tree_sha256") != self._tree_hash(report["inputs"])):
                    raise ValueError("改进版代码快照与运行记录不符")
                candidate_hashes = {
                    path: hashlib.sha256((optimized / path).read_bytes()).hexdigest() for path in actual_paths
                }
                if (any(edit["original_sha256"] != report["inputs"][edit["path"]]
                        or edit["candidate_sha256"] != candidate_hashes[edit["path"]] for edit in edits)
                        or any(candidate_hashes[path] != digest for path, digest in report["inputs"].items()
                               if path not in edit_paths)
                        or metadata.get("candidate_tree_sha256") != self._tree_hash(candidate_hashes)):
                    raise ValueError("候选算法文件与运行记录不符")
        else:
            for role, suffix in INPUT_SUFFIXES.items():
                content = (inputs / f"{role}{suffix}").read_bytes()
                if hashlib.sha256(content).hexdigest() != report["inputs"][role]:
                    raise ValueError("实验输入快照与记录的哈希不符")
        for trial in trials:
            if protocol.mode == "folder":
                if protocol.optimization_proposal_id or trial["role"] == "candidate":
                    expected_command = self.command_argv(protocol.command or "", protocol.main_file or "")
                else:
                    baseline = protocol.baseline_file or ""
                    expected_command = self.command_argv(
                        protocol.baseline_command or f"python {shlex.quote(baseline)}", baseline
                    )
            else:
                expected_command = ["python", "/input/experiment.py"]
            if trial.get("command") != expected_command:
                raise ValueError("试验命令与已确认协议不符")
            if protocol.optimization_proposal_id:
                key = "original_tree_sha256" if trial["role"] == "baseline" else "candidate_tree_sha256"
                expected_source = report["optimized"][key]
                if trial.get("source_sha256") != expected_source:
                    raise ValueError("原版与改进版运行来源不符")
            result_path = root / "runs" / run_id / "outputs" / trial["id"] / "result.json"
            info = result_path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
                raise ValueError("原始试验结果文件格式无效")
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            metrics = raw.get("metrics") if isinstance(raw, dict) else None
            value = metrics.get(protocol.metric) if isinstance(metrics, dict) else None
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not math.isfinite(value) or value != trial["value"]):
                raise ValueError("试验指标与原始文件不符")
        summary = self._summarize(trials)
        if report.get("summary") != summary or report.get("comparison") != self._comparison(summary, protocol):
            raise ValueError("实验汇总与原始试验不符")

        project = workflow._require_project(project_id)
        marker_path = root / "adopted.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.exists() else None
        original_name = f"experiment-{run_id}.json"
        existing = next(
            (item for item in project.state.get("documents", []) if item.get("name") == original_name
             and item.get("role") == "results"), None
        )
        if marker and marker["run_id"] != run_id:
            raise RuntimeError("本项目已有导入的实验报告；新方案请在新项目中验证")
        if existing and marker and marker["status"] == "adopted":
            return existing
        if any(item.get("role") == "results" and item is not existing
               for item in project.state.get("documents", [])):
            raise RuntimeError("项目已有其他结果资料；请在新项目中导入，避免混合版本")
        if not marker:
            self._write_json(marker_path, {"run_id": run_id, "status": "pending"})
        if existing is None:
            # 已持有项目锁，调用工作流的锁内入口避免锁重入。
            existing = workflow._add_document_locked(
                project_id, report_path, role="results", display_name=original_name,
                verified_experiment=True,
            )
        self._write_json(marker_path, {"run_id": run_id, "status": "adopted"})
        return existing

    @staticmethod
    def _summarize(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str], list[float]] = {}
        for trial in trials:
            if trial["status"] != "completed":
                continue
            key = (trial["role"], json.dumps(trial["parameters"], sort_keys=True))
            groups.setdefault(key, []).append(trial["value"])
        return [
            {"role": role, "parameters": json.loads(parameters),
             "mean": math.fsum(item / len(values) for item in values),
             "count": len(values)}
            for (role, parameters), values in groups.items()
        ]

    @staticmethod
    def _comparison(summary: list[dict[str, Any]], protocol: ExperimentProtocol) -> dict[str, Any]:
        candidates = [item for item in summary if item["role"] == "candidate"]
        best = (max if protocol.direction == "maximize" else min)(
            candidates, key=lambda item: item["mean"]
        )
        baseline = next(
            (item for item in summary if item["role"] == "baseline"
             and (not protocol.optimization_proposal_id or item["parameters"] == best["parameters"])),
            None,
        )
        difference = best["mean"] - baseline["mean"] if baseline else None
        if difference is not None and not math.isfinite(difference):
            raise ValueError("实验均值差值超出有限数值范围")
        return {
            "parameters": best["parameters"], "baseline_mean": baseline["mean"] if baseline else None,
            "candidate_mean": best["mean"], "difference": difference,
            "note": "仅供研究者比较均值；不代表显著性或因果结论。",
        }

    @staticmethod
    def _trial(
        run_dir: Path, role: str, variant: dict[str, Any], seed: int,
        protocol: ExperimentProtocol, image_id: str, trial_id: str,
        *, source_hash: str | None = None,
    ) -> dict[str, Any]:
        output = run_dir / "outputs" / trial_id
        output.mkdir(parents=True)
        result_path = output / "result.json"
        result_path.write_bytes(b"{}")
        result_path.chmod(0o666)
        if protocol.mode == "folder":
            trial_input = run_dir / "inputs" / (
                "optimized" if protocol.optimization_proposal_id and role == "candidate" else "project"
            )
            if protocol.optimization_proposal_id or role == "candidate":
                launch = ExperimentService.command_argv(protocol.command or "", protocol.main_file or "")
            else:
                launch = ExperimentService.command_argv(
                    protocol.baseline_command or f"python {shlex.quote(protocol.baseline_file or '')}",
                    protocol.baseline_file or "",
                )
            dataset = f"/input/{protocol.dataset_file}" if protocol.dataset_file else ""
        else:
            trial_input = run_dir / "trial-inputs" / trial_id
            trial_input.mkdir(parents=True)
            shutil.copyfile(run_dir / "inputs" / "dataset.csv", trial_input / "dataset.csv")
            shutil.copyfile(run_dir / "inputs" / f"{role}.py", trial_input / "experiment.py")
            launch = ["python", "/input/experiment.py"]
            dataset = "/input/dataset.csv"
        if os.name != "posix":
            raise RuntimeError("目前仅支持 POSIX 平台的 Docker 实验执行")
        if ":" in str(trial_input) or ":" in str(result_path):
            raise ValueError("实验工作区路径不能含冒号（Docker 挂载限制）")
        command = [
            "docker", "run", "--rm", "--name", f"scholaros-{trial_id}",
            "--log-driver", "none", "--shm-size", "1m",
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--workdir", "/input",
            "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "512m",
            "--cpus", "1", "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{trial_input.resolve()}:/input:ro",
            "-v", f"{result_path.resolve()}:/tmp/result.json:rw",
            "--ulimit", "fsize=1048576:1048576",
            "--env", f"SCHOLAROS_DATASET={dataset}",
            "--env", "SCHOLAROS_OUTPUT=/tmp/result.json",
            "--env", f"SCHOLAROS_SEED={seed}",
            "--env", f"SCHOLAROS_PARAMS={json.dumps(variant, ensure_ascii=False)}",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "PYTHONPATH=/input",
            image_id, *launch,
        ]
        trial = {"id": trial_id, "role": role, "parameters": variant, "seed": seed,
                 "command": launch}
        if source_hash:
            trial["source_sha256"] = source_hash
        try:
            result = subprocess.run(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=protocol.timeout_seconds, check=False,
            )
            trial["exit_code"] = result.returncode
            if result.returncode != 0:
                raise ValueError("脚本运行失败")
            info = result_path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
                raise ValueError("结果 JSON 超过 64 KiB")
            value = json.loads(result_path.read_text(encoding="utf-8"))
            metrics = value.get("metrics") if isinstance(value, dict) else None
            metric = metrics.get(protocol.metric) if isinstance(metrics, dict) else None
            if isinstance(metric, bool) or not isinstance(metric, (int, float)) or not math.isfinite(metric):
                raise ValueError("结果 JSON 缺少指定的有限数值指标")
            trial.update(status="completed", value=metric)
        except subprocess.TimeoutExpired as exc:
            if not ExperimentService._remove_container(f"scholaros-{trial_id}"):
                raise ContainerCleanupError("试验超时且容器清理未确认，请检查 Docker") from exc
            trial.update(status="failed", error=f"试验超时：{exc.timeout} 秒")
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            trial.update(status="failed", error=str(exc)[:500])
        finally:
            if result_path.exists():
                result_path.chmod(0o600)
        return trial
