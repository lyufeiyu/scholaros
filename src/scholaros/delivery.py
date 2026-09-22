from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from scholaros.domain import Project, utc_now
from scholaros.storage import ProjectStore
from scholaros.workspace import normalize_configuration

SUPPORTING_ARTIFACTS = (
    "papers.json",
    "evidence.json",
    "research-design.json",
    "figure-story.json",
    "table-story.json",
    "final-review.json",
)


def prepare_delivery(project: Project, store: ProjectStore) -> dict[str, Any]:
    paper_path = store.artifact_path(project.id, "paper.md")
    if paper_path is None:
        raise ValueError("完成稿尚未生成，不能准备交付包")
    markdown = paper_path.read_text(encoding="utf-8")
    config = normalize_configuration(project.state.get("configuration"))
    requested = list(config["formats"])

    if "docx" in requested:
        store.save_artifact(project.id, "paper.docx", markdown_to_docx(markdown))
    if "tex" in requested:
        store.save_artifact(
            project.id,
            "paper.tex",
            markdown_to_tex(markdown),
        )

    format_names = {"md": "paper.md", "docx": "paper.docx", "tex": "paper.tex", "pdf": "paper.pdf"}
    available_formats = [
        item for item in requested if store.artifact_path(project.id, format_names[item]) is not None
    ]
    missing_formats = [item for item in requested if item not in available_formats]
    blockers = []
    if not project.state.get("final_review", {}).get("passed"):
        blockers.append("最终质量检查尚未全部通过。")
    if missing_formats:
        blockers.append(f"缺少请求格式：{', '.join(missing_formats)}。")
    unresolved = [
        item
        for item in project.state.get("feedback", [])
        if item.get("status") in {"pending", "manual_required"}
    ]
    if unresolved:
        blockers.append(f"仍有 {len(unresolved)} 条返修意见需要处理。")
    if config["requested_scope"] == "submission_package":
        blockers.append("作者、单位、伦理、基金和利益冲突元数据需在投稿前由研究者确认。")

    manuscript_files = [format_names[item] for item in available_formats]
    package_files = list(manuscript_files)
    if config["requested_scope"] == "local_delivery":
        package_files.extend(SUPPORTING_ARTIFACTS)

    file_records = []
    for name in dict.fromkeys(package_files):
        path = store.artifact_path(project.id, name)
        if path is None:
            continue
        content = path.read_bytes()
        file_records.append(
            {
                "name": name,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "role": _artifact_role(name),
            }
        )
    template_files = _ieee_template_records() if "tex" in available_formats else []
    manifest = {
        "schema_version": 1,
        "project_id": project.id,
        "created_at": utc_now(),
        "scope": config["requested_scope"],
        "requested_formats": requested,
        "available_formats": available_formats,
        "missing_formats": missing_formats,
        "ready": not blockers,
        "blockers": blockers,
        "files": file_records,
        "template_files": template_files,
        "sharing_boundary": _sharing_boundary(config["requested_scope"]),
        "external_action": "本地交付不代表已投稿、已发布或获得外发授权。",
    }
    store.save_artifact(
        project.id,
        "delivery-manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2),
    )
    package = _delivery_zip(project.id, store, manifest)
    store.save_artifact(project.id, "delivery-package.zip", package)
    return manifest


def _sharing_boundary(scope: str) -> str:
    if scope == "local_delivery":
        return (
            "本地完整包包含证据、设计和内部审阅记录，可能含上传材料摘录；"
            "对外分享前必须人工检查。包内不含原始上传文件、密钥、数据库、历史版本或本机路径。"
        )
    return (
        "该范围只打包请求的稿件格式，不包含证据账本、内部审阅、原始上传文件、"
        "密钥、数据库、历史版本或本机路径。"
    )


def markdown_to_docx(markdown: str) -> bytes:
    """生成不依赖外部 Office 库的基础可编辑 DOCX。"""

    paragraphs = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line:
            paragraphs.append("<w:p/>")
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            size = max(24, 36 - len(heading.group(1)) * 2)
            paragraphs.append(_docx_paragraph(heading.group(2), bold=True, size=size))
        else:
            paragraphs.append(_docx_paragraph(_plain_markdown(line)))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
    return output.getvalue()


IEEE_TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "templates" / "ieee" / "IEEEtran"
IEEE_JOURNAL_TEMPLATE = IEEE_TEMPLATE_ROOT / "bare_jrnl.tex"
IEEE_TEMPLATE_FILES = (
    ("IEEEtran.cls", IEEE_TEMPLATE_ROOT / "IEEEtran.cls"),
    ("IEEEtran.bst", IEEE_TEMPLATE_ROOT / "bibtex" / "IEEEtran.bst"),
)


def _ieee_template_records() -> list[dict[str, Any]]:
    records = []
    for name, path in IEEE_TEMPLATE_FILES:
        if not path.is_file():
            continue
        content = path.read_bytes()
        records.append(
            {
                "name": name,
                "package_path": name,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "role": "latex_template",
            }
        )
    return records


def _ieee_template_source() -> str:
    try:
        return IEEE_JOURNAL_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return ""


def _ieee_document_class() -> str:
    """从仓库内保存的 IEEEtran 期刊模板读取文档类声明。"""

    template = _ieee_template_source()
    match = re.search(r"^\\documentclass(?:\[[^]]+\])?\{IEEEtran\}", template, re.MULTILINE)
    return match.group(0) if match else "\\documentclass[journal]{IEEEtran}"


def _ieee_package_lines() -> list[str]:
    """复用本地 bare_jrnl 模板中的实际宏包声明，避免只借用类名。"""

    template = _ieee_template_source()
    lines = re.findall(r"^\\usepackage(?:\[[^]]+\])?\{[^}]+\}", template, re.MULTILINE)
    return list(dict.fromkeys(lines))


def markdown_to_tex(markdown: str) -> str:
    """将 Markdown 草稿放入本地 IEEEtran 期刊模板骨架；输出统一为英文 IEEE 期刊格式。"""

    body: list[str] = []
    title = "ScholarOS Research Manuscript"
    commands = {1: "section", 2: "subsection", 3: "subsubsection"}
    first_heading = True
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            if first_heading and len(heading.group(1)) == 1:
                title = _tex_text(heading.group(2))
                first_heading = False
                continue
            first_heading = False
            level = min(3, len(heading.group(1)))
            body.append(f"\\{commands[level]}{{{_tex_text(heading.group(2))}}}")
        elif line.startswith("- "):
            body.append(f"\\textbullet\\ {_tex_text(line[2:])}\\par")
        elif line:
            first_heading = False
            body.append(f"{_tex_text(line)}\n")
        else:
            body.append("")
    template_packages = _ieee_package_lines()
    if not template_packages:
        template_packages = [
            "\\usepackage{amsmath,amssymb}",
            "\\usepackage{graphicx}",
            "\\usepackage{booktabs}",
            "\\usepackage{url}",
        ]
    if not any("hyperref" in line for line in template_packages):
        template_packages.append("\\usepackage[hidelinks]{hyperref}")
    return (
        f"{_ieee_document_class()}\n"
        + "\n".join(template_packages)
        + "\n"
        "\\title{" + title + "}\n"
        "\\author{ScholarOS Research Workspace}\n"
        "\\begin{document}\n"
        "\\maketitle\n"
        + "\n".join(body)
        + "\n\\end{document}\n"
    )


def _delivery_zip(project_id: str, store: ProjectStore, manifest: Mapping[str, Any]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in manifest["files"]:
            path = store.artifact_path(project_id, str(item["name"]))
            if path is not None:
                archive.writestr(path.name, path.read_bytes())
        for item in manifest.get("template_files", []):
            template_path = next(
                (path for name, path in IEEE_TEMPLATE_FILES if name == item.get("name")),
                None,
            )
            if template_path is not None and template_path.is_file():
                archive.writestr(str(item["package_path"]), template_path.read_bytes())
        archive.writestr(
            "delivery-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    return output.getvalue()


def _docx_paragraph(text: str, *, bold: bool = False, size: int = 22) -> str:
    properties = f'<w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/></w:rPr>'
    return f'<w:p><w:r>{properties}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _plain_markdown(value: str) -> str:
    text = re.sub(r"^[-*+]\s+", "• ", value)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def _tex_text(value: str) -> str:
    citations: dict[str, str] = {}

    def citation(match: re.Match[str]) -> str:
        token = f"SCHOLAROSCITE{len(citations)}TOKEN"
        citations[token] = f"\\cite{{{match.group(1)}}}"
        return token

    text = re.sub(r"\[@([A-Za-z0-9_.:-]+)\]", citation, _plain_markdown(value))
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    text = "".join(replacements.get(char, char) for char in text)
    for token, replacement in citations.items():
        text = text.replace(token, replacement)
    return text


def _artifact_role(name: str) -> str:
    if name.startswith("paper."):
        return "manuscript"
    if "review" in name:
        return "review"
    if "figure" in name:
        return "figure_plan"
    if name == "evidence.json" or name == "papers.json":
        return "evidence"
    return "supporting"
