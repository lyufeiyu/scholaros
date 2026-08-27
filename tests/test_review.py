from scholaros.domain import Evidence
from scholaros.review import PaperReviewer


def test_reviewer_rejects_unknown_citation() -> None:
    markdown = """# 标题
## 摘要
## 引言
## 相关工作
错误引用 [@Ghost2026]。
## 研究问题
## 方法
## 实验设计
## 图 1 设计说明
## 图 2 设计说明
## 表 1 设计说明
## 表 2 设计说明
## 局限
## 结论
## 参考文献
""" + ("正文" * 1500)
    evidence = [Evidence("Real2025", "真实", "摘要", [], [], None)]
    report = PaperReviewer().review(markdown, evidence)

    assert not report.passed
    assert any(item.code == "unknown_citation" for item in report.findings)


def test_reviewer_rejects_empty_evidence() -> None:
    report = PaperReviewer().review("# 摘要\n" + "正文" * 1500, [])
    assert not report.passed
    assert any(item.code == "no_evidence" for item in report.findings)


def test_reviewer_rejects_uncited_results_without_results_document() -> None:
    markdown = "实验表明模型准确率达到 99%，p < 0.001。\n" + "正文" * 1500
    evidence = [Evidence("Real2025", "真实", "摘要", [], [], None)]

    without_results = PaperReviewer().review(markdown, evidence)
    with_matching_results = PaperReviewer().review(
        markdown,
        evidence,
        result_sources=["原始分析输出：模型准确率达到 99%，p < 0.001。"],
    )
    with_unrelated_results = PaperReviewer().review(
        markdown,
        evidence,
        result_sources=["实验已经运行，但这份说明没有包含任何测量数值。"],
    )

    assert any(item.code == "fabricated_result_risk" for item in without_results.findings)
    assert not any(
        item.code == "fabricated_result_risk" for item in with_matching_results.findings
    )
    assert any(
        item.code == "fabricated_result_risk" for item in with_unrelated_results.findings
    )


def test_reviewer_does_not_treat_citation_as_source_for_current_results() -> None:
    markdown = "本文实验准确率达到 99% [@Real2025]。\n" + "正文" * 1500
    evidence = [Evidence("Real2025", "真实", "摘要", [], [], None)]

    report = PaperReviewer().review(markdown, evidence)

    assert any(item.code == "fabricated_result_risk" for item in report.findings)


def test_reviewer_checks_common_result_metrics_against_uploaded_results() -> None:
    markdown = "## 结果\nF1 score = 0.91; n=100。\n" + "正文" * 1500
    evidence = [Evidence("Real2025", "真实", "摘要", [], [], None)]

    report = PaperReviewer().review(markdown, evidence)

    assert any(item.code == "fabricated_result_risk" for item in report.findings)


def test_reviewer_rejects_same_numbers_with_different_result_meanings() -> None:
    markdown = "本文实验准确率达到 99%，p < 0.001。\n" + "正文" * 1500
    evidence = [Evidence("Real2025", "真实", "摘要", [], [], None)]

    report = PaperReviewer().review(
        markdown,
        evidence,
        result_sources=["实验包含 99 个样本，预处理阈值设为 0.001。"],
    )

    assert any(item.code == "fabricated_result_risk" for item in report.findings)


def test_reviewer_requires_citation_in_body_not_only_references() -> None:
    markdown = """# 标题
## 摘要
## 引言
正文没有文内引用。
## 相关工作
仍然没有文内引用。
## 研究问题
## 方法
## 实验设计
## 局限
## 结论
## 参考文献
- [@Real2025] 真实论文
""" + ("正文" * 1500)
    evidence = [Evidence("Real2025", "真实", "摘要", [], [], None)]

    report = PaperReviewer().review(markdown, evidence)

    assert any(item.code == "no_citations" for item in report.findings)
    assert report.metrics["citations_used"] == 0


def test_reviewer_recognizes_english_references_heading() -> None:
    markdown = """# 标题
## 摘要
## 引言
正文没有文内引用。
## References
- [@Real2025] Real paper
""" + ("正文" * 1500)
    evidence = [Evidence("Real2025", "真实", "摘要", [], [], None)]

    report = PaperReviewer().review(markdown, evidence)

    assert any(item.code == "no_citations" for item in report.findings)
    assert report.metrics["citations_used"] == 0


def test_reviewer_recognizes_bilingual_references_separator() -> None:
    markdown = """# 标题
## 摘要
## 引言
正文没有文内引用。
## 参考文献 / References
- [@Real2025] Real paper
""" + ("正文" * 1500)
    evidence = [Evidence("Real2025", "真实", "摘要", [], [], None)]

    report = PaperReviewer().review(markdown, evidence)

    assert any(item.code == "no_citations" for item in report.findings)
    assert report.metrics["citations_used"] == 0


def test_reviewer_does_not_pass_incomplete_medium_checks() -> None:
    markdown = """# 标题
## 摘要
研究方案摘要。
## 引言
研究背景 [@Real2025]。
## 相关工作
已有研究提供背景证据。
## 研究问题
如何验证该方法？
## 方法
预先规定纳入和排除规则、主要指标、分析计划与停止标准。
## 实验设计
执行对照实验。
## 图 1 设计说明
## 图 2 设计说明
## 表 1 设计说明
## 表 2 设计说明
## 局限
讨论限制。
## 结论
等待研究者验证。
## 研究者责任与 AI 辅助说明
AI 只提供辅助，研究者必须人工核验并承担最终责任。
## 参考文献
- [@Real2025] 真实论文
""" + ("正文" * 1500)
    evidence = [Evidence("Real2025", "真实", "摘要", [], [], None)]

    report = PaperReviewer().review(markdown, evidence)

    assert report.score >= 75
    assert report.metrics["check_figure_design"] == 0
    assert report.metrics["check_table_design"] == 0
    assert report.passed is False


def test_reviewer_requires_reproducible_method_and_researcher_responsibility() -> None:
    markdown = """# 标题
## 摘要
## 引言
## 相关工作
已有证据 [@Real2025]。
## 研究问题
## 方法
## 实验设计
## 图 1 设计说明
## 图 2 设计说明
## 表 1 设计说明
## 表 2 设计说明
## 局限
## 结论
## 参考文献
""" + ("正文" * 1500)
    evidence = [Evidence("Real2025", "真实", "摘要", [], [], None)]

    report = PaperReviewer().review(markdown, evidence)

    assert any(item.code == "method_reproducibility" for item in report.findings)
    assert any(item.code == "researcher_responsibility" for item in report.findings)
    assert report.metrics["quality_checks"] == 9
