from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from scholaros.domain import Evidence, ReviewFinding, ReviewReport


class PaperReviewer:
    required_sections = [
        "摘要",
        "引言",
        "相关工作",
        "研究问题",
        "方法",
        "实验设计",
        "局限",
        "结论",
        "参考文献",
    ]

    def review(
        self,
        markdown: str,
        evidence: Sequence[Evidence],
        *,
        result_sources: Sequence[str] = (),
    ) -> ReviewReport:
        findings: list[ReviewFinding] = []
        headings = re.findall(r"^#{1,6}\s+(.+)$", markdown, re.MULTILINE)
        missing_sections = []
        for required in self.required_sections:
            if not any(required in heading for heading in headings):
                missing_sections.append(required)
                findings.append(
                    ReviewFinding(
                        "high", "missing_section", f"缺少章节：{required}", "补齐该章节。"
                    )
                )

        allowed = {item.cite_key for item in evidence}
        all_used = set(re.findall(r"\[@([A-Za-z0-9_.:-]+)\]", markdown))
        body_used = set(
            re.findall(r"\[@([A-Za-z0-9_.:-]+)\]", _body_before_references(markdown))
        )
        if not allowed:
            findings.append(
                ReviewFinding(
                    "high",
                    "no_evidence",
                    "证据账本为空，研究草稿不能通过证据检查。",
                    "联网检索或上传合法资料后重新运行。",
                )
            )
        unknown = all_used - allowed
        for key in sorted(unknown):
            findings.append(
                ReviewFinding(
                    "high", "unknown_citation", f"引用键没有证据记录：{key}", "删除或补充证据账本。"
                )
            )
        if allowed and not body_used:
            findings.append(
                ReviewFinding(
                    "high", "no_citations", "论文没有使用证据账本引用。", "加入可追踪引用。"
                )
            )
        figure_design_complete = _has_complete_design_sections(markdown, "图")
        if not figure_design_complete:
            findings.append(
                ReviewFinding(
                    "medium", "figure_design", "图片设计说明少于 2 个。", "补齐目的、构成和图注。"
                )
            )
        table_design_complete = _has_complete_design_sections(markdown, "表")
        if not table_design_complete:
            findings.append(
                ReviewFinding(
                    "medium", "table_design", "表格设计说明少于 2 个。", "补齐字段和用途。"
                )
            )

        method_requirements = (
            r"纳入.{0,12}排除|排除.{0,12}纳入",
            r"主要(?:指标|结局)",
            r"分析计划|统计模型",
            r"失败条件|停止标准|证伪",
        )
        method_text = _sections_matching(
            markdown, lambda heading: any(term in heading for term in ("方法", "实验设计", "分析计划"))
        )
        method_complete = bool(method_text) and all(
            re.search(pattern, method_text) for pattern in method_requirements
        )
        if not method_complete:
            findings.append(
                ReviewFinding(
                    "medium",
                    "method_reproducibility",
                    "方法缺少纳入排除、主要指标、分析计划或失败/停止标准。",
                    "补齐可执行、可证伪并能由他人复核的方法协议。",
                )
            )

        responsibility_text = _sections_matching(
            markdown,
            lambda heading: "研究者责任" in heading
            and ("AI" in heading.upper() or "人工智能" in heading),
        )
        has_responsibility_statement = "最终责任" in responsibility_text and (
            "人工核验" in responsibility_text
            or "研究者核验" in responsibility_text
            or "人工审阅" in responsibility_text
        )
        responsibility_complete = bool(responsibility_text) and has_responsibility_statement
        if not responsibility_complete:
            findings.append(
                ReviewFinding(
                    "high",
                    "researcher_responsibility",
                    "缺少 AI 辅助范围与研究者最终责任说明。",
                    "说明 AI 只用于辅助，证据、方法、结果、署名和投稿由研究者核验并负责。",
                )
            )

        suspicious_lines = []
        result_claim = re.compile(
            r"(?:本研究|本文实验|实验结果|实验表明|结果表明|我们(?:的)?方法|本系统|准确率)"
            r".{0,100}(?:\d+(?:\.\d+)?\s*%|\bp\s*[<=>]\s*0?\.\d+)",
            re.IGNORECASE,
        )
        metric_claim = re.compile(
            r"(?:F1(?:\s*score)?|AUC|accuracy|precision|recall|准确率|精确率|召回率|"
            r"样本量|置信区间|confidence\s+interval|\bn\s*=)"
            r".{0,60}?[-+]?\d+(?:\.\d+)?",
            re.IGNORECASE,
        )
        current_study_claim = re.compile(r"本研究|本文实验|我们(?:的)?方法|本系统")
        results_section_text = _sections_matching(
            markdown,
            lambda heading: bool(
                re.search(r"(?:^|[\s.、])(?:结果|实验结果|results?)(?:$|[与和：:（(\s])", heading, re.I)
            ),
        )
        results_section_lines = set(results_section_text.splitlines())
        for line in markdown.splitlines():
            # 带证据键的一般文献结果可以跳过，但当前研究的结果不能靠引用键放行。
            looks_like_result = (
                result_claim.search(line)
                or metric_claim.search(line)
                or (line in results_section_lines and _result_numbers(line))
            )
            if looks_like_result and ("[@" not in line or current_study_claim.search(line)):
                suspicious_lines.append(line.strip())
        result_source_text = "\n".join(result_sources)
        source_result_signatures = _result_signatures(result_source_text)
        unsupported_result_lines = [
            line
            for line in suspicious_lines
            if not result_source_text
            or not _result_signatures(line)
            or not _result_signatures(line) <= source_result_signatures
        ]
        if unsupported_result_lines:
            findings.append(
                ReviewFinding(
                    "high",
                    "fabricated_result_risk",
                    "文稿中的结果性数值无法在已上传的 results 资料中定位。",
                    "删除无来源数值、补充包含原始结果的资料，或用证据键标明其来自已核验文献。",
                )
            )

        length = len(re.sub(r"\s+", "", markdown))
        if length < 2500:
            findings.append(
                ReviewFinding(
                    "medium", "short_draft", "正文过短，尚不足以支持完整论证。", "扩充论证与方法细节。"
                )
            )
        high = sum(item.severity == "high" for item in findings)
        medium = sum(item.severity == "medium" for item in findings)
        score = max(0, 100 - high * 20 - medium * 8)
        quality_checks = {
            "section_structure": not missing_sections,
            "evidence_available": bool(allowed),
            "citation_integrity": bool(allowed) and bool(body_used) and not unknown,
            "method_reproducibility": method_complete,
            "figure_design": figure_design_complete,
            "table_design": table_design_complete,
            "result_provenance": not unsupported_result_lines,
            "researcher_responsibility": responsibility_complete,
            "draft_depth": length >= 2500,
        }
        return ReviewReport(
            score=score,
            passed=all(quality_checks.values()),
            findings=findings,
            metrics={
                "characters": length,
                "headings": len(headings),
                "citations_used": len(body_used),
                "evidence_items": len(allowed),
                "high_findings": high,
                "medium_findings": medium,
                "quality_checks": len(quality_checks),
                "quality_checks_passed": sum(quality_checks.values()),
                **{
                    f"check_{name}": int(passed)
                    for name, passed in quality_checks.items()
                },
            },
        )


def _body_before_references(markdown: str) -> str:
    match = re.search(
        r"^#{1,6}\s+(?:\d+(?:\.\d+)*[.、]?\s*)?"
        r"(?:参考文献(?:\s*(?:[（(]\s*references?\s*[）)]|"
        r"[/／:：—–-]\s*references?|references?))?|references?|bibliography)\s*$",
        markdown,
        re.MULTILINE | re.IGNORECASE,
    )
    return markdown[: match.start()] if match else markdown


def _has_complete_design_sections(markdown: str, kind: str) -> bool:
    sections: dict[int, str] = {}
    pattern = re.compile(rf"^{kind}\s*([12一二])\s*设计说明(?:\s*[:：].*)?$")
    aliases = {"1": 1, "一": 1, "2": 2, "二": 2}
    for heading, body in _section_entries(markdown):
        if match := pattern.match(heading.strip()):
            sections[aliases[match.group(1)]] = body
    required_terms = (
        ("目的", "构成", "视觉编码", "图注")
        if kind == "图"
        else ("字段", "用途", "行设计", "分组", "标记规则")
    )
    return all(
        len(re.sub(r"\s+", "", sections.get(number, ""))) >= 40
        and sum(term in sections[number] for term in required_terms) >= 2
        for number in (1, 2)
    )


def _sections_matching(markdown: str, predicate) -> str:
    return "\n".join(
        body for heading, body in _section_entries(markdown) if predicate(heading)
    )


def _section_entries(markdown: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^(#{1,6})\s+(.+)$", markdown, re.MULTILINE))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        end = len(markdown)
        for next_match in matches[index + 1 :]:
            if len(next_match.group(1)) <= level:
                end = next_match.start()
                break
        sections.append((match.group(2).strip(), markdown[match.end() : end]))
    return sections


def _result_numbers(text: str) -> set[str]:
    text = re.sub(r"\[@[A-Za-z0-9_.:-]+\]", "", text)
    return {
        str(Decimal(value).normalize())
        for value in re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", text)
    }


def _result_signatures(text: str) -> set[tuple[str, str]]:
    clean = re.sub(r"\[@[A-Za-z0-9_.:-]+\]", "", text)
    clean = re.sub(r"\s+", " ", clean)
    number = r"([-+]?\d+(?:\.\d+)?)"
    metrics = {
        "accuracy": r"(?:accuracy|准确率)",
        "f1": r"(?:f1(?:\s*score)?)",
        "auc": r"(?:auc|area\s+under\s+(?:the\s+)?curve)",
        "precision": r"(?:precision|精确率)",
        "recall": r"(?:recall|召回率)",
        "sample_size": r"(?:sample\s+size|样本量|\bn\s*=)",
        "confidence_interval": r"(?:confidence\s+interval|置信区间|\bci\b)",
        "mean": r"(?:mean|average|平均值?|均值)",
        "standard_deviation": r"(?:standard\s+deviation|std\.?|标准差)",
        "change": r"(?:improvement|reduction|increase|decrease|提升|提高|降低|减少)",
    }
    signatures: set[tuple[str, str]] = set()
    for name, metric in metrics.items():
        patterns = (
            rf"{metric}.{{0,40}}?{number}",
            rf"{number}.{{0,20}}?{metric}",
        )
        for pattern in patterns:
            for value in re.findall(pattern, clean, re.IGNORECASE):
                signatures.add((name, str(Decimal(value).normalize())))
    for value in re.findall(rf"\bp\s*(?:value\s*)?[<=>]\s*{number}", clean, re.IGNORECASE):
        signatures.add(("p_value", str(Decimal(value).normalize())))
    return signatures
