from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from scholaros.delivery import IEEE_TEMPLATE_ROOT, markdown_to_tex, prepare_delivery
from scholaros.domain import Project
from scholaros.ieee_format import markdown_to_bib
from scholaros.storage import ProjectStore

SAMPLE = """# Traceable Evaluation of Research Agents

**Draft type:** A protocol, not measured results.

---

## Abstract
This protocol compares agents [@Source1] without inventing results.

**Keywords:** research agents; evidence; reliability.

---

## 1. Introduction
Sources must be checked before submission.

## 2. Related Work
A prior candidate [@Source1] motivates comparison.

## 3. Methods
Use matched retrieval budgets.

### Figure 1 Design Notes
The framework figure will show the pipeline.

## 4. Experimental Design
Predefine each comparison condition.

### Table 1 Design Notes
The planned table has no measured results.

## 5. Analysis Plan
Estimate uncertainty for all metrics.

## 6. Results Reporting Protocol
No result has been measured.

## 7. Discussion and Limitations
Original papers require verification.

## 8. Conclusion
This is only a protocol.

## References
- [@Source1] A. Author. (2024). *A Reference Title*. Journal Name. https://doi.org/10.1234/example
"""

PLAN = {
    "datasets": ["Benchmark A: selection pending verification", "Benchmark B: pending"],
    "baselines": ["Single-agent RAG: same retriever", "Majority-vote debate: matched budget"],
    "metrics": ["Accuracy: higher is better", "ECE: lower is better", "Latency: lower is better"],
}


def test_ieee_sample_markdown_has_keywords_header_tables_and_figure_note() -> None:
    tex = markdown_to_tex(SAMPLE, template="ieee_journal", comparison_plan=PLAN)
    assert tex.startswith("\\documentclass[lettersize,journal]{IEEEtran}")
    assert "\\markboth{" in tex and "Vol.~XX, No.~XX, Month~Year" in tex
    assert "\\begin{IEEEkeywords}\nresearch agents, evidence, reliability\n\\end{IEEEkeywords}" in tex
    assert "Keywords:" not in tex.split("\\end{abstract}")[0]
    assert "Draft type" not in tex and "---" not in tex
    assert "\\section{Introduction}" in tex and "\\section{1. Introduction}" not in tex
    assert "\\textcolor{blue}{Figure 1:" in tex
    assert "\\begin{table}[!t]" in tex and "\\begin{table*}[!t]" in tex
    assert "Benchmark A" in tex and "Majority-vote debate" in tex and "ECE" in tex
    assert tex.count("\\textit{pending}") >= 4
    assert "\\bibliographystyle{IEEEtran}\n\\bibliography{ref}" in tex
    assert "\\bibitem" not in tex
    assert re.search(r"\\cite\{Source1\}", tex)


def test_ieee_bib_preserves_source_fields_and_unverified_entries() -> None:
    bib = markdown_to_bib(SAMPLE)
    assert "@misc{Source1," in bib
    assert "title = {{A Reference Title}}" in bib
    assert "url = {https://doi.org/10.1234/example}" in bib
    url = markdown_to_bib(
        "# Title\n\n## References\n- [@Y] A. Author. (2024). *Title*. Venue. "
        "https://example.org/a_b%20c"
    )
    assert "url = {https://example.org/a_b%20c}" in url
    raw = markdown_to_bib("# Title\n\n## References\n1. Unknown source.\n   Pending details.")
    assert "@misc{UnverifiedRef1," in raw
    assert "Pending details" in raw


def test_markdown_comparison_table_becomes_latex_float_without_literal_pipes() -> None:
    source = SAMPLE.replace(
        "Predefine each comparison condition.",
        "Predefine each comparison condition.\n\n|Dataset|Baseline|Accuracy|ECE|\n|---|---|---|---|\n|Benchmark A|RAG|pending|pending|",
    )
    tex = markdown_to_tex(source, template="ieee_journal", comparison_plan=PLAN)
    assert "Benchmark A & RAG & pending & pending" in tex
    assert "|---|" not in tex
    assert "\\resizebox{\\columnwidth}" in tex
    assert tex.count("\\begin{table}") == 2  # 数据集概况 + 文稿已有对比表


def test_ieee_delivery_zip_has_main_tex_bib_and_only_required_runtime_files(settings) -> None:
    store = ProjectStore(settings)
    project = Project(id="ab12ab12ab12", idea="Compare research agents")
    project.state["configuration"] = {"output_language": "en", "latex_template": "ieee_journal"}
    project.state["design"] = PLAN
    project.state["final_review"] = {"passed": True}
    store.save_artifact(project.id, "paper.md", SAMPLE)
    manifest = prepare_delivery(project, store)
    assert manifest["ready"], manifest["blockers"]
    assert {item["name"] for item in manifest["template_files"]} == {"IEEEtran.cls", "IEEEtran.bst"}
    tex_record = next(item for item in manifest["files"] if item["name"] == "paper.tex")
    assert tex_record["package_path"] == "main.tex"
    with zipfile.ZipFile(io.BytesIO(store.artifact_path(project.id, "delivery-package.zip").read_bytes())) as archive:
        names = set(archive.namelist())
        assert {"main.tex", "ref.bib", "IEEEtran.cls", "IEEEtran.bst"} <= names
        assert "paper.tex" not in names and "bare_jrnl.tex" not in names
        assert "\\bibliography{ref}" in archive.read("main.tex").decode("utf-8")
        assert "@misc{Source1," in archive.read("ref.bib").decode("utf-8")
    assert {p.name for p in Path(IEEE_TEMPLATE_ROOT).iterdir()} == {"IEEEtran.cls", "IEEEtran.bst"}
    cls = (IEEE_TEMPLATE_ROOT / "IEEEtran.cls").read_text(encoding="latin-1")
    assert r"\def\LARGE{\@setfontsize{\LARGE}{18}{20pt}}" in cls

def test_unverified_numeric_table_is_rejected_without_results() -> None:
    markdown = SAMPLE.replace(
        "Predefine each comparison condition.",
        "Predefine each comparison condition.\n\n|Method|Accuracy|\n|---|---|\n|Ours|92.4|",
    )
    with pytest.raises(ValueError, match="未提供结果资料的数值"):
        markdown_to_tex(markdown, template="ieee_journal", comparison_plan=PLAN)
    tex = markdown_to_tex(
        markdown, template="ieee_journal", comparison_plan={**PLAN, "results_available": True},
    )
    assert "Ours & 92.4" in tex


def test_bibliography_without_in_text_citation_blocks_ready(settings) -> None:
    store = ProjectStore(settings)
    project = Project(id="1234abcd5678", idea="No cited entries")
    project.state["configuration"] = {"output_language": "en", "latex_template": "ieee_journal"}
    project.state["design"] = PLAN
    project.state["final_review"] = {"passed": True}
    markdown = SAMPLE.replace("agents [@Source1]", "agents").replace("candidate [@Source1]", "candidate")
    store.save_artifact(project.id, "paper.md", markdown)
    manifest = prepare_delivery(project, store)
    assert not manifest["ready"]
    assert any("正文没有引用" in issue for issue in manifest["blockers"])


def test_bib_keeps_ambiguous_author_literal_and_escapes_tex_symbols() -> None:
    source = (
        "# Title\n\n## References\n"
        "- [@X] Smith, J., Doe, A. (2024). *C^2 & Cost_1*. IEEE Journal. "
        "https://doi.org/10.1234/x"
    )
    bib = markdown_to_bib(source)
    assert "author = {{Smith, J., Doe, A.}}" in bib
    assert "Smith and J." not in bib
    assert r"title = {{C\textasciicircum{}2 \& Cost\_1}}" in bib


def test_table_covers_dataset_method_and_metrics_and_preserves_figure_note() -> None:
    tex = markdown_to_tex(SAMPLE, template="ieee_journal", comparison_plan=PLAN)
    assert "Dataset & Method & Accuracy & ECE & Latency" in tex
    assert "Benchmark A & Proposed method" in tex
    assert "Benchmark B & Majority-vote debate" in tex
    assert "The framework figure will show the pipeline" in tex
    assert "\\textcolor{blue}{Figure 1:" in tex


def test_escaped_pipe_in_markdown_table_and_results_role_without_table() -> None:
    source = SAMPLE.replace(
        "Predefine each comparison condition.",
        "Predefine each comparison condition.\n\n"
        "|Method|Metric|\n|---|---|\n|RAG|budget \\| latency|",
    )
    tex = markdown_to_tex(source, template="ieee_journal", comparison_plan=PLAN)
    assert r"RAG & budget | latency" in tex
    without_table = markdown_to_tex(
        SAMPLE, template="ieee_journal", comparison_plan={**PLAN, "results_available": True},
    )
    assert r"\begin{table*}" in without_table
    assert r"\textit{pending}" in without_table

def test_malformed_markdown_table_blocks_instead_of_losing_structure() -> None:
    source = SAMPLE.replace(
        "Predefine each comparison condition.",
        "Predefine each comparison condition.\n\n|Method|Metric|\n|---|---|\n|RAG|pending|extra|",
    )
    with pytest.raises(ValueError, match="列数不一致"):
        markdown_to_tex(source, template="ieee_journal", comparison_plan=PLAN)
