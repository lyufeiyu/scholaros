from __future__ import annotations

import pytest

from scholaros.cli import _print_pending_search_plan, build_parser
from scholaros.domain import Paper, Project
from scholaros.papers import PaperSearchService, paper_web_links
from scholaros.terminal import TerminalUI, _create_project_wizard, _delete_project_wizard
from scholaros.workflow import ResearchWorkflow


def test_terminal_delete_requires_retyping_project_id(settings, monkeypatch, capsys) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证终端删除项目需要输入项目编号二次确认")
    artifact = workflow.store.save_artifact(project.id, "paper.md", "# disposable")
    answers = iter([project.id, project.id])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    _delete_project_wizard(settings, TerminalUI())

    assert workflow.store.get_project(project.id) is None
    assert not artifact.parent.exists()
    assert "已删除" in capsys.readouterr().out


def test_terminal_delete_wrong_confirmation_keeps_project(settings, monkeypatch, capsys) -> None:
    workflow = ResearchWorkflow(settings, search=PaperSearchService([]))
    project = workflow.create_project("验证终端输入错误项目编号时会取消删除操作")
    answers = iter([project.id, "wrong-id"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    _delete_project_wizard(settings, TerminalUI())

    assert workflow.store.get_project(project.id) is not None
    assert "已取消删除" in capsys.readouterr().out


def test_terminal_offline_project_can_complete_without_search_results(
    settings, monkeypatch
) -> None:
    answers = iter([
        "离线演示科研智能体如何提高引用可靠性",
        "offline",
        "",
        "",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    _create_project_wizard(settings, TerminalUI())

    projects = ResearchWorkflow(settings, search=PaperSearchService([])).store.list_projects()
    assert len(projects) == 1
    assert projects[0].status.value == "needs_attention"
    store = ResearchWorkflow(settings, search=PaperSearchService([])).store
    assert store.artifact_path(projects[0].id, "paper.md") is not None


def test_paper_web_links_use_safe_landing_doi_and_pdf_urls() -> None:
    paper = Paper(
        title="Safe links",
        authors=[],
        year=2026,
        abstract="",
        sources=["memory"],
        external_id="safe-links",
        doi="10.1000/a-b",
        landing_url="javascript:alert(1)",
        pdf_url="https://example.org/paper.pdf",
    )

    assert paper_web_links(paper) == [
        ("网页", "https://doi.org/10.1000/a-b"),
        ("开放 PDF", "https://example.org/paper.pdf"),
    ]

    paper.doi = "not-a-doi"
    assert paper_web_links(paper) == [
        ("开放 PDF", "https://example.org/paper.pdf")
    ]


def test_cli_rejects_search_limit_outside_supported_range() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["search", "agents", "--limit", "-1"])


def test_cli_accepts_structured_search_field() -> None:
    args = build_parser().parse_args(
        ["search", "Geoffrey Hinton", "--field", "author", "--source", "dblp"]
    )

    assert args.field == "author"
    assert args.sources == ["dblp"]


def test_cli_accepts_author_disambiguation_filters() -> None:
    args = build_parser().parse_args(
        [
            "search",
            "Wei Wang",
            "--field",
            "author",
            "--affiliation",
            "Shenzhen University",
            "--topic",
            "computer vision",
            "--venue",
            "CVPR",
        ]
    )

    assert args.affiliation == "Shenzhen University"
    assert args.topic == "computer vision"
    assert args.venue == "CVPR"


def test_cli_accepts_search_plan_confirmation_command() -> None:
    args = build_parser().parse_args(["confirm-search", "0123456789ab"])

    assert args.command == "confirm-search"
    assert args.project_id == "0123456789ab"


def test_cli_accepts_search_plan_rejection_with_revised_idea() -> None:
    args = build_parser().parse_args(
        ["reject-search", "0123456789ab", "--idea", "明确研究局部特征选择与 LLM 的结合"]
    )

    assert args.command == "reject-search"
    assert "局部特征选择" in args.idea


def test_cli_pending_plan_output_shows_queries_warnings_and_actions(capsys) -> None:
    project = Project(
        id="0123456789ab",
        idea="研究 secret sharing 与大语言模型的结合方式",
        state={
            "search_confirmation_required": True,
            "search_queries": ["secret sharing", "LLM secret sharing"],
            "search_plan_warnings": ["请特别核对。"],
        },
    )

    _print_pending_search_plan(project, TerminalUI())

    output = capsys.readouterr().out
    assert "secret sharing" in output
    assert "请特别核对" in output
    assert "confirm-search 0123456789ab" in output
    assert "reject-search 0123456789ab" in output
