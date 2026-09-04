from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import quote

from scholaros.config import Settings
from scholaros.domain import Project, SearchField, Stage
from scholaros.papers import (
    build_google_scholar_query,
    paper_web_links,
    search_match_policy,
)
from scholaros.terminal import WorkflowReporter, interactive, serve
from scholaros.workflow import ResearchWorkflow


def _search_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit 必须是整数") from exc
    if not 1 <= limit <= 100:
        raise argparse.ArgumentTypeError("limit 必须在 1 到 100 之间")
    return limit


def _print_pending_search_plan(project: Project, ui) -> None:
    ui.warning("检索计划正在等待确认，尚未向第三方论文源发送。")
    for warning in project.state.get("search_plan_warnings", []):
        ui.warning(warning)
    for index, query in enumerate(project.state.get("search_queries", []), 1):
        print(f"  {index}. {query}")
    print(f"确认后继续：scholaros confirm-search {project.id}")
    print(f"拒绝并修改：scholaros reject-search {project.id} --idea '新的研究说明'")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scholaros", description="ScholarOS Research Assistant Workspace"
    )
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="从研究想法生成可审阅的 Markdown 研究草稿")
    run.add_argument("idea")
    run.add_argument("--source", action="append", dest="sources", default=[])
    run.add_argument("--document", action="append", type=Path, default=[])
    run.add_argument("--results", action="append", type=Path, default=[])
    run.add_argument("--offline", action="store_true", help="不联网检索，生成结构化演示稿")
    run.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    run.add_argument("--guided", action="store_true", help="在范围、证据、方法和初稿完成后等待确认")

    for command, help_text in (("resume", "从当前断点继续"), ("approve", "确认引导阶段并继续"),
                               ("history", "列出重做前保存的历史版本")):
        action = subparsers.add_parser(command, help=help_text)
        action.add_argument("project_id")
    rerun = subparsers.add_parser("rerun", help="只重做指定阶段及其下游，保留历史版本")
    rerun.add_argument("project_id")
    rerun.add_argument("--from-stage", required=True, choices=[s.value for s in Stage if s != Stage.COMPLETED])

    search = subparsers.add_parser("search", help="跨源搜索论文")
    search.add_argument("query")
    search.add_argument("--source", action="append", dest="sources", default=[])
    search.add_argument(
        "--field",
        choices=[field.value for field in SearchField],
        default=SearchField.ALL.value,
        help="检索字段：all、title、author、doi 或 venue",
    )
    search.add_argument("--limit", type=_search_limit, default=20)
    search.add_argument(
        "--affiliation",
        help="作者检索附加条件：学校或机构（须能绑定到命中姓名的同一作者）",
    )
    search.add_argument("--topic", help="作者检索附加条件：论文主题关键词")
    search.add_argument("--venue", help="作者检索附加条件：期刊或会议，如 CVPR")
    search.add_argument(
        "--natural-language",
        action="store_true",
        help="先用已配置模型把自然语言想法转换为英文学术检索词",
    )
    search.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    show = subparsers.add_parser("show", help="查看项目状态")
    show.add_argument("project_id")

    confirm = subparsers.add_parser("confirm-search", help="确认待外发检索计划并继续项目")
    confirm.add_argument("project_id")

    reject = subparsers.add_parser("reject-search", help="拒绝检索计划并按修改说明重新界定")
    reject.add_argument("project_id")
    reject.add_argument("--idea", required=True, help="修改后的研究想法或核心问题")

    serve = subparsers.add_parser("serve", help="启动 Web 与 API 服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    if args.command is None:
        try:
            interactive(settings)
        except (KeyboardInterrupt, EOFError):
            print("\n已退出 ScholarOS。")
        return
    workflow = ResearchWorkflow(settings)
    if args.command == "run":
        from scholaros.terminal import TerminalUI

        ui = TerminalUI()
        if not args.json:
            ui.banner()
            workflow.progress_sink = WorkflowReporter(ui)
        if args.offline:
            from scholaros.papers import PaperSearchService
            from scholaros.writing import ResearchWriter

            workflow.search = PaperSearchService([])
            workflow.writer = ResearchWriter()
            workflow.allow_empty_search = True
        project = workflow.create_project(args.idea, args.sources, guided=args.guided)
        for document in args.document:
            workflow.add_document(project.id, document)
        for results in args.results:
            workflow.add_document(project.id, results, role="results")
        result = asyncio.run(workflow.run(project.id))
        paper = workflow.store.artifact_path(result.id, "paper.md")
        if args.json:
            print(
                json.dumps(
                    {"project": result.to_dict(), "paper": str(paper)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            if result.state.get("pending_checkpoint"):
                ui.info(f"阶段 {result.state['pending_checkpoint']} 等待确认。")
                print(f"查看：scholaros show {result.id}")
                print(f"继续：scholaros approve {result.id}")
            elif result.state.get("search_confirmation_required"):
                _print_pending_search_plan(result, ui)
            else:
                ui.success(f"项目 {result.id}：{result.status.value}")
                ui.success(f"论文已保存：{paper}")
    elif args.command == "search":

        async def search_papers():
            plan = await workflow.prepare_search_plan(
                args.query,
                natural_language=args.natural_language,
                field=args.field,
            )
            result = await workflow.search.search(
                plan.search_query,
                limit=args.limit,
                selected=args.sources or None,
                field=plan.field,
                author_affiliation=args.affiliation,
                author_topic=args.topic,
                author_venue=args.venue,
            )
            return plan, result

        try:
            plan, result = asyncio.run(search_papers())
        except ValueError as exc:
            build_parser().error(str(exc))
        if args.json:
            plan_value = {
                **plan.to_dict(),
                "matching_policy": search_match_policy(
                    plan.field,
                    author_affiliation=args.affiliation,
                    author_topic=args.topic,
                    author_venue=args.venue,
                ),
                "author_filters": {
                    "affiliation": args.affiliation,
                    "topic": args.topic,
                    "venue": args.venue,
                },
                "google_scholar_query": build_google_scholar_query(
                    plan.search_query,
                    plan.field,
                    author_affiliation=args.affiliation,
                    author_topic=args.topic,
                    author_venue=args.venue,
                ),
            }
            print(
                json.dumps(
                    {
                        **plan_value,
                        "search_plan": plan_value,
                        "papers": [paper.to_dict() for paper in result.papers],
                        "failures": [item.to_dict() for item in result.failures],
                        "filtered_out": result.filtered_out,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"检索字段：{plan.field.value}")
            if plan.natural_language:
                print(f"原始输入：{plan.input_query}")
                print(f"模型生成的英文检索词：{'；'.join(plan.search_terms)}")
            print(f"最终发送的检索式：{plan.search_query}")
            if plan.field == SearchField.AUTHOR:
                print(f"学校/机构：{args.affiliation or '未限制'}")
                print(f"主题关键词：{args.topic or '未限制'}")
                print(f"期刊/会议：{args.venue or '未限制'}")
            print(
                "匹配规则："
                + search_match_policy(
                    plan.field,
                    author_affiliation=args.affiliation,
                    author_topic=args.topic,
                    author_venue=args.venue,
                )
            )
            if result.filtered_out:
                print(f"严格匹配已排除 {result.filtered_out} 条宽泛候选记录。")
            for index, paper in enumerate(result.papers, 1):
                print(f"{index:>2}. {paper.title} ({paper.year or 'n.d.'})")
                if paper.venue:
                    print(f"    期刊/会议：{paper.venue}")
                links = paper_web_links(paper)
                if not links:
                    print("    网页：当前来源未提供")
                for label, url in links:
                    print(f"    {label}：{url}")
            for failure in result.failures:
                print(f"! {failure.source}: {failure.reason}")
                print(f"  应对方案：{failure.suggestion}")
            print(
                "Google Scholar 手动补充检索："
                "https://scholar.google.com/scholar?q="
                + quote(
                    build_google_scholar_query(
                        plan.search_query,
                        plan.field,
                        author_affiliation=args.affiliation,
                        author_topic=args.topic,
                        author_venue=args.venue,
                    ),
                    safe="",
                )
            )
    elif args.command in {"resume", "rerun", "approve", "history"}:
        try:
            if args.command == "history":
                print(json.dumps(workflow.store.list_history(args.project_id), ensure_ascii=False, indent=2))
                return
            operation = (
                workflow.resume(args.project_id) if args.command == "resume"
                else workflow.approve_and_run(args.project_id) if args.command == "approve"
                else workflow.rerun_from(args.project_id, args.from_stage)
            )
            result = asyncio.run(operation)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        except (KeyError, ValueError, RuntimeError) as exc:
            raise SystemExit(str(exc)) from exc
    elif args.command == "show":
        project = workflow.store.get_project(args.project_id)
        if project is None:
            raise SystemExit(f"项目不存在：{args.project_id}")
        value = project.to_dict()
        value["artifacts"] = workflow.store.list_artifacts(args.project_id)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif args.command == "confirm-search":
        try:
            result = asyncio.run(workflow.confirm_search_plan_and_run(args.project_id))
        except (KeyError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        paper = workflow.store.artifact_path(result.id, "paper.md")
        print(f"项目 {result.id}：{result.status.value}")
        print(f"论文：{paper or '尚未生成'}")
    elif args.command == "reject-search":
        try:
            result = asyncio.run(
                workflow.reject_search_plan_and_run(
                    args.project_id, revised_idea=args.idea
                )
            )
        except (KeyError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        print(f"项目 {result.id}：{result.status.value}")
        print("旧计划已拒绝；新计划如下：")
        if result.state.get("search_confirmation_required"):
            from scholaros.terminal import TerminalUI

            _print_pending_search_plan(result, TerminalUI())
    elif args.command == "serve":
        serve(settings, args.host, args.port)


if __name__ == "__main__":
    main()
