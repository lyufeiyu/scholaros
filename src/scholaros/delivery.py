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
            markdown_to_tex(markdown, template=config["latex_template"]),
        )

    format_names = {"md": "paper.md", "docx": "paper.docx", "tex": "paper.tex", "pdf": "paper.pdf"}
    available_formats = [
        item for item in requested if store.artifact_path(project.id, format_names[item]) is not None
    ]
    missing_formats = [item for item in requested if item not in available_formats]
    blockers = []
    warnings = []
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
    tex_path = store.artifact_path(project.id, "paper.tex")
    ieee_requested = config["latex_template"] == "ieee_journal"
    ieee_generated = (
        tex_path is not None
        and "\\documentclass[journal]{IEEEtran}" in tex_path.read_text(encoding="utf-8").splitlines()[:2]
    )
    if ieee_requested and tex_path is not None and not ieee_generated:
        blockers.append("稿件含非英文内容，无法应用所选 IEEE 期刊模板；请修订英文稿后重新生成。")
    if ieee_requested:
        warnings.append("IEEE 期刊稿仅提供排版骨架；作者信息、引文、图表及目标期刊规则需人工核验。")
    if config["requested_scope"] == "submission_package":
        warnings.append("作者、单位、伦理、基金和利益冲突元数据需在投稿前由研究者确认。")

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
    template_files = (
        _ieee_template_records()
        if "tex" in available_formats and ieee_requested and ieee_generated
        else []
    )
    if ieee_requested and ieee_generated:
        if {item["name"] for item in template_files} != {"IEEEtran.cls", "IEEEtran.bst"}:
            blockers.append("IEEE 模板资源不完整；交付包不可独立编译。")
        tex_source = tex_path.read_text(encoding="utf-8")
        cited = set(re.findall(r"\\cite\{([^}]+)\}", tex_source))
        referenced = set(re.findall(r"\\bibitem\{([^}]+)\}", tex_source))
        unresolved = sorted(cited - referenced)
        if unresolved:
            blockers.append(f"IEEE 稿有未匹配参考文献的引文键：{', '.join(unresolved)}。")
        if any(key.startswith("UnverifiedRef") for key in referenced):
            blockers.append("编号参考文献缺少可核验引用键；请为条目补充 [@cite_key]。")
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
        "warnings": warnings,
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


IEEE_TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates" / "ieee"
IEEE_JOURNAL_TEMPLATE = IEEE_TEMPLATE_ROOT / "bare_jrnl.tex"
IEEE_TEMPLATE_FILES = (
    ("IEEEtran.cls", IEEE_TEMPLATE_ROOT / "IEEEtran.cls"),
    ("IEEEtran.bst", IEEE_TEMPLATE_ROOT / "IEEEtran.bst"),
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


def markdown_to_tex(markdown: str, *, template: str = "generic") -> str:
    """把 Markdown 草稿转成通用或 IEEE 期刊 LaTeX；不虚构作者/参考文献信息。"""

    if template not in {"generic", "ieee_journal"}:
        raise ValueError("未知 LaTeX 模板")
    requested_ieee = template == "ieee_journal"
    if requested_ieee and _contains_cjk(markdown):
        template = "generic"
    title, body, abstract, keywords, references = _tex_body(markdown)
    if template == "ieee_journal":
        document_class = _ieee_document_class()
        packages = _ieee_package_lines()
        if not packages:
            packages = ["\\usepackage{amsmath,amssymb}", "\\usepackage{graphicx}", "\\usepackage{url}"]
        if not any("hyperref" in line for line in packages):
            packages.append("\\usepackage[hidelinks]{hyperref}")
    else:
        document_class = "\\documentclass{ctexart}" if _contains_cjk(markdown) else "\\documentclass{article}"
        packages = [
            "\\usepackage{amsmath,amssymb}", "\\usepackage{graphicx}",
            "\\usepackage{booktabs}", "\\usepackage{url}",
            "\\usepackage[hidelinks]{hyperref}",
        ]
    preface = []
    if abstract:
        preface.extend(["\\begin{abstract}", *abstract, "\\end{abstract}"])
    if keywords:
        if template == "ieee_journal":
            preface.extend(["\\begin{IEEEkeywords}", *keywords, "\\end{IEEEkeywords}"])
        else:
            preface.append("\\noindent\\textbf{Keywords:} " + " ".join(keywords) + "\\par")
    if references:
        body.extend(["\\begin{thebibliography}{" + str(len(references)) + "}",
                     *[f"\\bibitem{{{key}}} {value}" for key, value in references],
                     "\\end{thebibliography}"])
    return (
        ("% 所选 IEEE 模板未应用：稿件含非英文内容，请修订后重新生成。\n"
         if requested_ieee and template == "generic" else "")
        + f"{document_class}\n"
        + "\n".join(packages)
        + "\n"
        "\\title{" + title + "}\n"
        "\\author{Author information to be confirmed}\n"
        "\\begin{document}\n"
        "\\maketitle\n"
        + "\n".join(preface + body)
        + "\n\\end{document}\n"
    )


def _tex_body(markdown: str) -> tuple[str, list[str], list[str], list[str], list[tuple[str, str]]]:
    """识别常见 Markdown 章节；IEEE 稿使用原生摘要、关键词和参考文献环境。"""

    body: list[str] = []
    abstract: list[str] = []
    keywords: list[str] = []
    references: list[tuple[str, str]] = []
    title = "ScholarOS Research Manuscript"
    section = "body"
    commands = {1: "section", 2: "subsection", 3: "subsubsection"}
    seen_heading = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            name = heading.group(2).strip().casefold()
            if not seen_heading and len(heading.group(1)) == 1:
                title = _tex_text(heading.group(2))
                seen_heading = True
                continue
            seen_heading = True
            if name in {"abstract", "摘要"}:
                section = "abstract"
            elif name in {"keywords", "index terms", "关键词"}:
                section = "keywords"
            elif name in {"references", "bibliography", "参考文献"}:
                section = "references"
            else:
                section = "body"
                level = min(3, max(1, len(heading.group(1)) - 1))
                body.append(f"\\{commands[level]}{{{_tex_text(heading.group(2))}}}")
            continue
        if not line.strip():
            if section == "body":
                body.append("")
            continue
        if section == "references":
            entry = re.match(
                r"^\s*(?:[-*]|\d+[.)])\s+\[@([A-Za-z0-9_.:-]+)\]\s*(.+)$", line
            )
            numbered = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
            if entry:
                references.append((entry.group(1), _tex_text(entry.group(2))))
            elif numbered:
                # 保留无键条目，但不伪造与正文引文的对应关系。
                references.append((f"UnverifiedRef{len(references) + 1}", _tex_text(numbered.group(1))))
            elif line[:1].isspace() and references:
                key, text = references[-1]
                references[-1] = (key, text + " " + _tex_text(line.strip()))
            else:
                body.append("% 待人工核对的非结构化参考文献：" + _tex_text(line))
        elif section == "abstract":
            abstract.append(_tex_text(line))
        elif section == "keywords":
            keywords.append(_tex_text(line))
        elif line.startswith("- "):
            body.append(f"\\textbullet\\ {_tex_text(line[2:])}\\par")
        else:
            body.append(_tex_text(line))
    return title, body, abstract, keywords, references


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


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
