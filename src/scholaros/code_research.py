"""只读扫描上传目录中的源码，建立模块索引并按行号拆成模型可处理的片段。"""

from __future__ import annotations

import ast
import hashlib
from collections import deque
from pathlib import Path
from typing import Any

from scholaros.locals import LocalRunService

MAX_CODE_BYTES = 1024 * 1024
CHUNK_CHARS = 16_000
README_ANALYSIS_CHARS = 30_000
SOURCE_SUFFIXES = {".py", ".sh", ".yaml", ".yml", ".toml"}


def inspect_run_readme(runs: LocalRunService, run_id: str) -> dict[str, Any]:
    """只读取一个大小写不敏感的 README.md，不扫描目录中的其他文件。"""
    directory = runs._require_record(run_id)
    files = sorted(runs._read_record(directory)["file_hashes"])
    readmes = [name for name in files
               if len(Path(name).parts) == 1 and Path(name).name.casefold() == "readme.md"]
    if not readmes:
        raise ValueError("上传目录中没有 README.md；当前分析只读取这一个文件，不会读取其他文件")
    selected = min(readmes, key=lambda name: (len(Path(name).parts), name.casefold()))
    if not runs._workspace_file(directory, selected):
        raise ValueError(f"README.md 不存在或已变成符号链接：{selected}")
    path = directory / "workspace" / selected
    with path.open("rb") as handle:
        raw = handle.read(MAX_CODE_BYTES + 1)
    if len(raw) > MAX_CODE_BYTES:
        raise ValueError("README.md 超过 1 MiB，当前不能保证逐行完整分析；请先缩小该文件")
    if b"\x00" in raw:
        raise ValueError("README.md 不是可读文本")
    text = raw.decode("utf-8-sig", errors="replace")
    lines = len(text.splitlines())
    if not text.strip():
        raise ValueError("README.md 为空，无法形成研究想法")
    if len(text) > README_ANALYSIS_CHARS:
        raise ValueError("根目录 README.md 过长；请控制在 30,000 字符以内，当前只进行一次快速分析")
    outline = {"path": selected, "lines": lines, "symbols": [], "imports": [], "calls": []}
    chunks = [f"[文件 {selected}:L1-L{lines}]\n{text}\n"]
    ranges = [[{"path": selected, "start": 1, "end": lines}]]
    digest = hashlib.sha256(selected.encode("utf-8") + raw).hexdigest()
    return {
        "digest": digest, "bytes": len(raw), "files": [selected],
        "outlines": [outline], "chunks": chunks, "ranges": ranges,
        "ignored_data_files": len(files) - 1,
    }


def inspect_run_code(runs: LocalRunService, run_id: str) -> dict[str, Any]:
    """源码全部读取或明确拒绝；数据集和无关文档不进入模型上下文。"""
    directory = runs._require_record(run_id)
    files = sorted(runs._read_record(directory)["file_hashes"])
    candidates = [name for name in files if Path(name).suffix.lower() in SOURCE_SUFFIXES
                  or Path(name).name.lower() in {"readme.md", "readme.txt"}]
    contents: dict[str, str] = {}
    outlines: dict[str, dict[str, Any]] = {}
    total = 0
    digest = hashlib.sha256()
    for name in candidates:
        if not runs._workspace_file(directory, name):
            raise ValueError(f"代码文件不存在或已变成符号链接：{name}")
        path = directory / "workspace" / name
        with path.open("rb") as handle:
            raw = handle.read(MAX_CODE_BYTES - total + 1)
        total += len(raw)
        if total > MAX_CODE_BYTES:
            raise ValueError("源码总量超过 1 MiB，当前不能保证逐行完整分析；请先选择仅含相关源码的目录")
        if b"\x00" in raw:
            raise ValueError(f"代码文件不是可读文本：{name}")
        text = raw.decode("utf-8-sig", errors="replace")
        contents[name] = text
        digest.update(name.encode("utf-8"))
        digest.update(raw)
        outline: dict[str, Any] = {"path": name, "lines": len(text.splitlines()), "symbols": [], "imports": [], "calls": []}
        if name.endswith(".py"):
            try:
                tree = ast.parse(text, filename=name)
            except SyntaxError as exc:
                outline["syntax_warning"] = f"第 {exc.lineno} 行不能静态解析；仍会按原文分析"
            else:
                outline["symbols"] = [
                    {"name": node.name, "line": node.lineno, "kind": type(node).__name__}
                    for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ][:120]
                outline["imports"] = list(dict.fromkeys(
                    alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names
                ))[:80] + list(dict.fromkeys(
                    (node.module or "") for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                ))[:80]
                outline["calls"] = list(dict.fromkeys(
                    node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
                    for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, (ast.Attribute, ast.Name))
                ))[:80]
        outlines[name] = outline
    if not contents:
        raise ValueError("目录中没有可分析的代码或 README")
    entry = [name for name in contents if Path(name).name in {"main.py", "run.py", "train.py", "__main__.py"}]
    stem_map = {Path(name).stem: name for name in contents if name.endswith(".py")}
    ordered: list[str] = []
    queue = deque(entry)
    while queue:
        name = queue.popleft()
        if name in ordered:
            continue
        ordered.append(name)
        for imported in outlines[name]["imports"]:
            match = stem_map.get(imported.rsplit(".", 1)[-1])
            if match and match not in ordered:
                queue.append(match)
    ordered.extend(name for name in contents if name not in ordered)
    chunks: list[str] = []
    ranges: list[list[dict[str, Any]]] = []
    current = ""
    current_ranges: list[dict[str, Any]] = []
    for name in ordered:
        text = contents[name]
        for offset in range(0, len(text), CHUNK_CHARS - 400):
            part = text[offset:offset + CHUNK_CHARS - 400]
            start_line = text.count("\n", 0, offset) + 1
            end_line = start_line + part.count("\n")
            block = f"[文件 {name}:L{start_line}-L{end_line}]\n" + part
            if current and len(current) + len(block) + 1 > CHUNK_CHARS:
                chunks.append(current)
                ranges.append(current_ranges)
                current = ""
                current_ranges = []
            current += block + "\n"
            current_ranges.append({"path": name, "start": start_line, "end": end_line})
    if current:
        chunks.append(current)
        ranges.append(current_ranges)
    if not chunks:
        raise ValueError("源码和说明文件都是空的，无法分析技术实现")
    return {
        "digest": digest.hexdigest(), "bytes": total, "files": ordered,
        "outlines": [outlines[name] for name in ordered], "chunks": chunks, "ranges": ranges,
        "ignored_data_files": len(files) - len(candidates),
    }
