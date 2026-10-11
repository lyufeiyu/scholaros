from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

CITE_KEY = r"[A-Za-z0-9_.:-]+"


def _escape(value: str) -> str:
    """仅转换受支持的轻量 Markdown，不把原始 Markdown 符号送入 TeX。"""

    citations: dict[str, str] = {}

    def cite(match: re.Match[str]) -> str:
        marker = f"SCHOLAROSCITE{len(citations)}TOKEN"
        citations[marker] = f"\\cite{{{match.group(1)}}}"
        return marker

    text = re.sub(rf"\[@({CITE_KEY})\]", cite, value)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    symbols = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    text = "".join(symbols.get(char, char) for char in text)
    for marker, command in citations.items():
        text = text.replace(marker, command)
    return text


def reference_entries(markdown: str) -> list[tuple[str, str]]:
    """只从参考文献段提取键及原始条目，不推断未给出的书目信息。"""

    entries: list[tuple[str, str]] = []
    in_references = False
    for line in markdown.splitlines():
        heading = re.match(r"^#{2,6}\s+(.+)$", line)
        if heading:
            heading_name = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", heading.group(1)).casefold()
            in_references = heading_name in {"references", "bibliography", "参考文献"}
            continue
        if not in_references or not line.strip() or re.fullmatch(r"\s*[-*_]{3,}\s*", line):
            continue
        keyed = re.match(rf"^\s*(?:[-*]|\d+[.)])\s+\[@({CITE_KEY})\]\s*(.+)$", line)
        unkeyed = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        if keyed:
            entries.append((keyed.group(1), keyed.group(2).strip()))
        elif unkeyed:
            entries.append((f"UnverifiedRef{len(entries) + 1}", unkeyed.group(1).strip()))
        elif line[:1].isspace() and entries:
            key, text = entries[-1]
            entries[-1] = (key, text + " " + line.strip())
    return entries


def _bib_value(text: str) -> str:
    """保护 BibTeX 特殊字符；不改变题名或作者的事实内容。"""

    symbols = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "_": r"\_",
        "#": r"\#", "{": r"\{", "}": r"\}", "$": r"\$",
        "^": r"\textasciicircum{}", "~": r"\textasciitilde{}",
    }
    return "".join(symbols.get(char, char) for char in text)


def markdown_to_bib(markdown: str) -> str:
    """生成独立的 ref.bib；解析失败的条目原样放在 note 中等待核验。"""

    output = []
    seen = set()
    for key, raw in reference_entries(markdown):
        if key in seen:
            continue
        seen.add(key)
        structured = re.match(
            r"^(.*?)\.\s*\((\d{4}|n\.d\.)\)\.\s*\*([^*]+)\*\.\s*(.+?)\.\s*(https?://\S+)?$",
            raw,
        )
        fields: dict[str, str] = {}
        if structured:
            authors, year, title, venue, url = structured.groups()
            if authors.strip():
                # “Smith, J., Doe, A.” 有歧义；按原文保留，不能拆成四位作者。
                if re.search(r",\s*[A-Z]\.", authors):
                    names = authors.strip()
                    if re.search(r",\s*[A-Z]$", names):
                        names += "."
                    fields["author"] = "{" + names + "}"
                else:
                    fields["author"] = " and ".join(part.strip() for part in authors.split(", "))
            fields["title"] = title.strip()
            if year != "n.d.":
                fields["year"] = year
            fields["howpublished"] = venue.strip()
            if url:
                doi = re.search(r"https?://(?:dx\.)?doi\.org/(\S+)", url, re.I)
                # IEEEtran.bst 不处理独立 doi 字段；使用它支持的 url 字段。
                fields["url"] = "https://doi.org/" + doi.group(1) if doi else url
        else:
            fields["note"] = raw
        lines = [f"@misc{{{key},"]
        for field, value in fields.items():
            # IEEEtran.bst 自行用 \url 排版 URL；保留其中的下划线与百分号。
            escaped = value.replace("{", r"\{").replace("}", r"\}") if field == "url" else _bib_value(value)
            if field == "title" or (field == "author" and value.startswith("{")):
                escaped = "{" + _bib_value(value.strip("{}")) + "}"
            lines.append(f"  {field} = {{{escaped}}},")
        lines.append("}")
        output.append("\n".join(lines))
    return "\n\n".join(output) + ("\n" if output else "")


def _short_label(text: str, *, words: int = 5) -> str:
    label = text.split(":", 1)[0].strip()
    label = re.sub(r"\s+", " ", label)
    return " ".join(label.split()[:words])[:56] or "To be selected"


def _plan_values(plan: Mapping[str, Any], key: str, fallback: str, limit: int) -> list[str]:
    items = plan.get(key)
    values = [
        _short_label(item) for item in items
        if isinstance(item, str) and item.strip() and not re.search(r"[\u3400-\u9fff]", item)
    ] if isinstance(items, list) else []
    return list(dict.fromkeys(values))[:limit] or [fallback]


def _planned_tables(plan: Mapping[str, Any] | None) -> list[str]:
    """按数据集、方法与指标数选择单双栏；所有实验值只写待填。"""

    plan = plan or {}
    datasets = _plan_values(plan, "datasets", "Dataset to be confirmed", 5)
    baselines = _plan_values(plan, "baselines", "Baseline to be confirmed", 6)
    metrics = _plan_values(plan, "metrics", "Primary metric to be defined", 4)
    if metrics == ["Primary metric to be defined"]:
        metrics = _plan_values(plan, "dependent_variables", metrics[0], 4)
    proposed = plan.get("proposed_method")
    if not isinstance(proposed, str) or re.search(r"[\u3400-\u9fff]", proposed):
        proposed = "Proposed method"
    methods = [_short_label(proposed), *baselines]
    pending = r"\textit{pending}"
    dataset_rows = "\n".join(f"{_escape(item)} & {pending} & {pending} \\\\" for item in datasets)
    width = "table*" if len(metrics) >= 2 or len(datasets) > 1 else "table"
    table_width = r"\textwidth" if width == "table*" else r"\columnwidth"
    header = "Dataset & Method & " + " & ".join(_escape(item) for item in metrics) + r" \\"
    comparison_rows = "\n".join(
        f"{_escape(dataset)} & {_escape(method)} & "
        + " & ".join([pending] * len(metrics)) + r" \\"
        for dataset in datasets for method in methods
    )
    return [
        "\\begin{table}[!t]\n\\centering\n\\caption{Planned Dataset Characteristics (Values Pending)}\n\\label{tab:datasets}\n\\footnotesize\n\\begin{tabular}{lcc}\n\\hline\nDataset & Samples & Split \\\\\n\\hline\n" + dataset_rows + "\n\\hline\n\\end{tabular}\n\\end{table}",
        f"\\begin{{{width}}}[!t]\n\\centering\n\\caption{{Planned Dataset--Method Comparison (Cells Pending Verification)}}\n\\label{{tab:comparison}}\n\\footnotesize\n\\resizebox{{{table_width}}}{{!}}{{%\n\\begin{{tabular}}{{ll{'c' * len(metrics)}}}\n\\hline\n" + header + "\n\\hline\n" + comparison_rows + f"\n\\hline\n\\end{{tabular}}%\n}}\n\\end{{{width}}}",
    ]


def _markdown_table(lines: list[str], *, results_available: bool) -> str:
    """保留已有表格；无结果资料时拒绝把数值当实验结果。"""

    rows = [
        [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        for line in lines
    ]
    columns = len(rows[0])
    if columns < 2 or any(len(row) != columns for row in rows):
        raise ValueError("IEEE 稿含列数不一致的 Markdown 表格，请修正后重新生成。")
    rows = [row for row in rows if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in row)]
    if not results_available and any(
        re.match(r"^[-+]?\d+(?:\.\d+)?(?:\s*[%({+-].*)?$", cell)
        for row in rows[1:] for cell in row[1:]
    ):
        raise ValueError("IEEE 表格含未提供结果资料的数值；请改为 pending 或补充真实结果。")
    environment = "table*" if columns > 4 else "table"
    width = r"\textwidth" if columns > 4 else r"\columnwidth"
    content = " & ".join(_escape(cell) for cell in rows[0]) + r" \\"
    content += "\n\\hline\n" + "\n".join(
        " & ".join(_escape(cell) for cell in row) + r" \\" for row in rows[1:]
    )
    return (
        f"\\begin{{{environment}}}[!t]\n\\centering\n"
        "\\caption{Comparison Table (Verify Source Data)}\n\\footnotesize\n"
        f"\\resizebox{{{width}}}{{!}}{{%\n\\begin{{tabular}}{{{'l' + 'c' * (columns - 1)}}}\n"
        + "\\hline\n" + content + f"\n\\hline\n\\end{{tabular}}%\n}}\n\\end{{{environment}}}"
    )

def render_ieee_tex(markdown: str, *, comparison_plan: Mapping[str, Any] | None = None, journal_name: str = "") -> str:
    """生成紧凑的 IEEEtran 期刊骨架，不冒充期刊元数据或实验结果。"""

    title = "Research Manuscript"
    abstract: list[str] = []
    keywords: list[str] = []
    body: list[str] = []
    section = "front"
    plan = comparison_plan or {}
    skip_notes = False
    figure_note_index: int | None = None
    table_lines: list[str] = []
    had_markdown_table = False
    heading_levels = {2: "section", 3: "subsection", 4: "subsubsection"}
    for line in markdown.splitlines():
        line = line.strip()
        if table_lines and not (line.startswith("|") and line.endswith("|")):
            body.append(_markdown_table(table_lines, results_available=bool(plan.get("results_available"))))
            table_lines = []
        if section == "body" and not skip_notes and line.startswith("|") and line.endswith("|"):
            table_lines.append(line)
            had_markdown_table = True
            continue
        if not line or re.fullmatch(r"[-*_]{3,}", line):
            if section == "body" and not skip_notes:
                body.append("")
            continue
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            level = len(match.group(1))
            heading = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", match.group(2).strip())
            if level == 1 and title == "Research Manuscript":
                title = heading
                continue
            name = heading.casefold()
            skip_notes = False
            figure_note_index = None
            if name in {"abstract", "摘要"}:
                section = "abstract"
            elif name in {"keywords", "index terms", "关键词"}:
                section = "keywords"
            elif name in {"references", "bibliography", "参考文献"}:
                section = "references"
            else:
                section = "body"
                figure = re.fullmatch(r"Figure\s+(\d+)\s+Design Notes(?::.*)?", heading, re.I)
                table = re.fullmatch(r"Table\s+(\d+)\s+Design Notes(?::.*)?", heading, re.I)
                if figure:
                    body.append(r"\noindent\textcolor{blue}{Figure " + figure.group(1) + ": planned illustration; authors will insert and verify the figure.}\\par")
                    skip_notes = True
                    figure_note_index = len(body) - 1
                elif table:
                    skip_notes = True
                else:
                    command = heading_levels.get(level, "subsubsection")
                    body.append(f"\\{command}{{{_escape(heading)}}}")
            continue
        inline_keywords = re.match(r"^(?:\*\*)?(?:Keywords|Index Terms)(?:\*\*)?\s*:(?:\*\*)?\s*(.+)$", line, re.I)
        if inline_keywords:
            keywords.append(_escape(re.sub(r";\s*", ", ", inline_keywords.group(1)).rstrip(".")))
            continue
        if skip_notes:
            if figure_note_index is not None:
                detail = re.sub(r"^\*\*(?:Purpose|Composition|Caption)\s*:\*\*\s*", "", line, flags=re.I)
                detail = " ".join(detail.split())
                if len(detail) > 140:
                    detail = detail[:140].rsplit(" ", 1)[0]
                number = re.search(r"Figure (\d+)", body[figure_note_index]).group(1)
                body[figure_note_index] = (
                    rf"\noindent\textcolor{{blue}}{{Figure {number}: {_escape(detail)} "
                    r"(illustration to be added and verified by the authors).}\par"
                )
                figure_note_index = None
            continue
        if section == "references":
            continue
        if section == "front":
            if line.startswith("**"):
                continue
            section = "body"
        if section == "abstract":
            abstract.append(_escape(line))
        elif section == "keywords":
            keywords.append(_escape(re.sub(r";\s*", ", ", line).rstrip(".")))
        elif line.startswith("- "):
            body.append(r"\noindent\textbullet\ " + _escape(line[2:]) + r"\par")
        else:
            body.append(_escape(line))
    if table_lines:
        body.append(_markdown_table(table_lines, results_available=bool(plan.get("results_available"))))
    if not any(r"\textcolor{blue}{Figure " in line for line in body):
        placeholder = (
            r"\noindent\textcolor{blue}{Figure 1 (framework): authors will add "
            r"and verify an illustration of the proposed workflow.}\par"
        )
        position = next(
            (index for index, value in enumerate(body) if value.startswith(r"\section{Methods}")),
            len(body),
        )
        body.insert(position + 1, placeholder)
    tables = _planned_tables(plan)
    if had_markdown_table:
        tables = tables[:1]
    position = next((index for index, value in enumerate(body) if value.startswith(r"\section{Experimental Design}")), len(body))
    body[position + 1:position + 1] = tables
    journal_label = journal_name.strip()
    if re.search(r"[\u3400-\u9fff]", journal_label):
        journal_label = ""
    journal = _escape(journal_label or "Journal name to be confirmed")
    short_title = _escape(title[:65])
    return (
        "\\documentclass[lettersize,journal]{IEEEtran}\n"
        "\\usepackage{amsmath,amssymb}\n\\usepackage{array}\n\\usepackage{graphicx}\n"
        "\\usepackage{url}\n\\usepackage{cite}\n\\usepackage{xcolor}\n"
        "\\begin{document}\n"
        f"\\title{{{_escape(title)}}}\n"
        "\\author{Author information to be confirmed}\n"
        f"\\markboth{{{journal},~Vol.~XX, No.~XX, Month~Year}}{{{short_title}}}\n"
        "\\maketitle\n"
        "\\begin{abstract}\n" + "\n".join(abstract) + "\n\\end{abstract}\n"
        "\\begin{IEEEkeywords}\n" + "\n".join(keywords) + "\n\\end{IEEEkeywords}\n"
        + "\n".join(body)
        + "\n\\bibliographystyle{IEEEtran}\n\\bibliography{ref}\n\\end{document}\n"
    )