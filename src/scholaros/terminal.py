from __future__ import annotations

import asyncio
import json
import shlex
import sys
from pathlib import Path
from urllib.parse import quote

from scholaros.config import Settings
from scholaros.domain import Project, ProjectStatus, SearchField, Stage
from scholaros.papers import (
    PaperSearchService,
    build_google_scholar_query,
    paper_web_links,
    search_match_policy,
)
from scholaros.storage import ProjectBusyError
from scholaros.workflow import ResearchWorkflow
from scholaros.writing import ResearchWriter

STAGE_LABELS = {
    Stage.SCOPING.value: "收敛研究问题",
    Stage.SEARCHING.value: "跨源检索论文",
    Stage.SYNTHESIZING.value: "建立证据账本",
    Stage.DESIGNING.value: "设计研究方法",
    Stage.DRAFTING.value: "起草研究稿件",
    Stage.REVIEWING.value: "执行质量检查",
    Stage.REVISING.value: "辅助修订稿件",
    Stage.COMPLETED.value: "工作流完成",
}
STAGE_NUMBERS = {stage.value: index for index, stage in enumerate(ResearchWorkflow.stage_order, 1)}
STATUS_LABELS = {
    ProjectStatus.CREATED: "待运行",
    ProjectStatus.RUNNING: "运行中",
    ProjectStatus.COMPLETED: "已通过",
    ProjectStatus.NEEDS_ATTENTION: "待人工确认",
    ProjectStatus.FAILED: "运行失败",
}


class TerminalUI:
    """零额外依赖的中文终端界面；非交互输出时自动关闭颜色。"""

    def __init__(self) -> None:
        self.color = sys.stdout.isatty()

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def banner(self) -> None:
        print()
        print(self.paint("  ScholarOS", "1;36"), self.paint("Research Assistant Workspace", "2"))
        print(self.paint("  证据追踪、方法设计与研究写作辅助", "2"))
        print("  " + "─" * 52)

    def title(self, value: str) -> None:
        print(f"\n{self.paint(value, '1;36')}")

    def info(self, value: str) -> None:
        print(f"  {self.paint('●', '36')} {value}")

    def success(self, value: str) -> None:
        print(f"  {self.paint('✓', '32')} {value}")

    def warning(self, value: str) -> None:
        print(f"  {self.paint('!', '33')} {value}")

    def error(self, value: str) -> None:
        print(f"  {self.paint('×', '31')} {value}")

    def ask(self, prompt: str, default: str | None = None) -> str:
        suffix = f" [{default}]" if default else ""
        value = input(f"  {prompt}{suffix}: ").strip()
        return value or (default or "")

    def choose(self, prompt: str, valid: set[str]) -> str:
        while True:
            value = self.ask(prompt).lower()
            if value in valid:
                return value
            self.warning(f"请输入：{' / '.join(sorted(valid))}")


class WorkflowReporter:
    def __init__(self, ui: TerminalUI):
        self.ui = ui

    def __call__(self, event_type: str, payload: dict[str, object]) -> None:
        if event_type == "stage_started":
            stage = str(payload.get("stage", ""))
            index = STAGE_NUMBERS.get(stage, 0)
            label = STAGE_LABELS.get(stage, stage)
            print(f"  {self.ui.paint(f'[{index}/7]', '1;36')} {label}…")
        elif event_type == "workflow_failed":
            self.ui.error(str(payload.get("error", "工作流失败")))


def interactive(settings: Settings) -> None:
    ui = TerminalUI()
    ui.banner()
    model_status = f"已配置模型：{settings.model}" if settings.api_key else "未配置模型，将诚实降级"
    ui.info(model_status)
    if settings.api_key:
        thinking = {
            "auto": "跟随模型默认",
            "enabled": "强制开启",
            "disabled": "关闭",
        }[settings.model_thinking]
        ui.info(
            f"模型调用：非流式完整响应 · 深度思考{thinking} · "
            f"读取超时 {settings.model_timeout:g} 秒"
        )
    while True:
        print("\n  1  新建论文项目")
        print("  2  搜索论文")
        print("  3  查看已有项目")
        print("  4  删除已有项目")
        print("  5  启动网页工作台")
        print("  g  新建引导式研究项目")
        print("  q  退出")
        choice = ui.choose("请选择", {"1", "2", "3", "4", "5", "g", "q"})
        if choice == "1":
            _create_project_wizard(settings, ui)
        elif choice == "2":
            _search_wizard(settings, ui)
        elif choice == "3":
            _show_projects(settings, ui)
        elif choice == "4":
            _delete_project_wizard(settings, ui)
        elif choice == "5":
            _serve(settings, ui)
        elif choice == "g":
            _create_project_wizard(settings, ui, guided=True)
        else:
            print("\n  再见。研究过程已保存在 .scholaros 中。\n")
            return


def _create_project_wizard(settings: Settings, ui: TerminalUI, *, guided: bool = False) -> None:
    ui.title("新建论文项目")
    while True:
        idea = ui.ask("研究想法或核心问题")
        if len(idea) >= 8:
            break
        ui.warning("请至少输入 8 个字符，让系统能收敛研究问题。")
    sources_text = ui.ask(
        "论文源（英文逗号分隔，offline 表示离线）",
        "arxiv,openalex,crossref,dblp,acm",
    )
    source_documents = _ask_paths(ui, "参考资料路径（可留空，可拖入多个文件）")
    result_documents = _ask_paths(ui, "真实结果资料路径（可留空）")
    offline = sources_text.strip().lower() == "offline"
    sources = [] if offline else [item.strip() for item in sources_text.split(",") if item.strip()]

    ui.title("开始执行")
    workflow = ResearchWorkflow(settings, progress_sink=WorkflowReporter(ui))
    if offline:
        workflow.search = PaperSearchService([])
        workflow.writer = ResearchWriter()
        workflow.allow_empty_search = True
    try:
        project = workflow.create_project(idea, sources, guided=guided)
        for path in source_documents:
            workflow.add_document(project.id, path, role="source")
        for path in result_documents:
            workflow.add_document(project.id, path, role="results")
        result = asyncio.run(workflow.run(project.id))
        while result.state.get("search_confirmation_required") or result.state.get("pending_checkpoint"):
            if pending := result.state.get("pending_checkpoint"):
                ui.title(f"阶段确认：{STAGE_LABELS.get(pending, pending)}")
                key = {"scoping": "spec", "synthesizing": "evidence", "designing": "design"}.get(pending)
                if key:
                    print(json.dumps(result.state[key], ensure_ascii=False, indent=2))
                else:
                    ui.info(f"请先阅读：{workflow.store.artifact_path(project.id, 'paper-draft.md')}")
                decision = ui.choose("y=确认继续；r=重做本阶段；q=保存并返回", {"y", "r", "q"})
                if decision == "q":
                    break
                result = asyncio.run(
                    workflow.approve_and_run(project.id) if decision == "y"
                    else workflow.rerun_from(project.id, pending)
                )
                continue
            ui.title("确认外发检索计划")
            ui.info("以下词组尚未发送给任何第三方论文源：")
            for warning in result.state.get("search_plan_warnings", []):
                ui.warning(warning)
            for index, query in enumerate(result.state.get("search_queries", []), 1):
                print(f"  {index}. {query}")
            decision = ui.choose("确认这些检索词并继续？（y=继续；n=拒绝并修改）", {"y", "n"})
            if decision == "n":
                revised_idea = ui.ask("修改后的研究想法（直接回车则暂不处理）")
                if not revised_idea:
                    _show_result(workflow, result, ui)
                    return
                result = asyncio.run(
                    workflow.reject_search_plan_and_run(
                        project.id, revised_idea=revised_idea
                    )
                )
            else:
                result = asyncio.run(workflow.confirm_search_plan_and_run(project.id))
    except Exception as exc:
        ui.error(f"{type(exc).__name__}: {exc}")
        return
    _show_result(workflow, result, ui)


def _search_wizard(settings: Settings, ui: TerminalUI) -> None:
    ui.title("跨源论文检索")
    ui.info("系统先并行获取候选，再严格核验字段命中并按 DOI 或标题合并去重。")
    field_choice = ui.choose(
        "检索字段（1=综合主题；2=标题；3=作者；4=DOI；5=期刊/会议）",
        {"1", "2", "3", "4", "5"},
    )
    field = {
        "1": SearchField.ALL,
        "2": SearchField.TITLE,
        "3": SearchField.AUTHOR,
        "4": SearchField.DOI,
        "5": SearchField.VENUE,
    }[field_choice]
    prompt = {
        SearchField.ALL: "关键词或自然语言研究问题（支持中文和英文）",
        SearchField.TITLE: "完整论文标题（一般标点规范化，技术符号保留）",
        SearchField.AUTHOR: "作者全名（姓氏须一致；结果侧名字可缩写为 G. Hinton）",
        SearchField.DOI: "DOI（例如 10.1145/1234567）",
        SearchField.VENUE: "期刊或会议名称（例如 Nature、CVPR、AAAI、NeurIPS/NIPS）",
    }[field]
    query = ui.ask(prompt)
    if len(query) < 2:
        ui.warning("检索式至少需要 2 个字符。")
        return
    mode = (
        ui.choose("检索方式（1=原样检索；2=自然语言转英文学术检索词）", {"1", "2"})
        if field == SearchField.ALL
        else "1"
    )
    author_affiliation = None
    author_topic = None
    author_venue = None
    if field == SearchField.AUTHOR:
        ui.info("同名较多时可追加以下 AND 条件；直接回车表示不限制。")
        author_affiliation = ui.ask("作者学校/机构（建议填官方英文名称）") or None
        author_topic = ui.ask("论文主题关键词") or None
        author_venue = ui.ask("期刊/会议（支持 CVPR、AAAI、NIPS/NeurIPS 等简称）") or None
    sources_text = ui.ask(
        "论文源（回车=全部默认源；也可填单个或逗号分隔多个）",
        "arxiv,openalex,crossref,dblp,acm,ieee",
    )
    sources = [item.strip() for item in sources_text.split(",") if item.strip()]
    workflow = ResearchWorkflow(settings)
    ieee_status = next(
        item["status"] for item in workflow.search.catalog() if item["name"] == "ieee"
    )
    ui.info(f"IEEE 状态：{ieee_status}")

    async def search():
        plan = await workflow.prepare_search_plan(
            query, natural_language=mode == "2", field=field
        )
        result = await workflow.search.search(
            plan.search_query,
            limit=12,
            selected=sources,
            field=plan.field,
            author_affiliation=author_affiliation,
            author_topic=author_topic,
            author_venue=author_venue,
        )
        return plan, result

    ui.info("正在理解检索意图并并行查询，请稍候…" if mode == "2" else "正在并行检索，请稍候…")
    try:
        plan, result = asyncio.run(search())
    except Exception as exc:
        ui.error(f"检索失败：{exc}")
        return
    if mode == "2":
        ui.title("可核对的检索计划")
        print(f"  原始输入：{plan.input_query}")
        print(f"  模型生成的英文检索词：{'；'.join(plan.search_terms)}")
        print(f"  最终发送给论文库的检索式：{plan.search_query}")
    else:
        ui.info(f"检索字段：{plan.field.value}；最终检索值：{plan.search_query}")
    if field == SearchField.AUTHOR:
        ui.info(
            "作者附加条件："
            f"机构={author_affiliation or '未限制'}；"
            f"主题={author_topic or '未限制'}；"
            f"会议={author_venue or '未限制'}"
        )
    ui.info(
        "匹配规则："
        + search_match_policy(
            plan.field,
            author_affiliation=author_affiliation,
            author_topic=author_topic,
            author_venue=author_venue,
        )
    )
    if result.filtered_out:
        ui.info(f"严格匹配已排除 {result.filtered_out} 条宽泛候选记录。")
    if not result.papers:
        message = (
            "来源返回了候选，但均未通过严格匹配；请核对完整姓名/标题或补充主题词。"
            if result.filtered_out
            else "没有返回论文。请调整检索词或检查网络。"
        )
        ui.warning(message)
    for index, paper in enumerate(result.papers, 1):
        year = paper.year or "年份未知"
        source = ", ".join(paper.sources)
        print(f"\n  {index:>2}. {ui.paint(paper.title, '1')}")
        venue = f" · {paper.venue}" if paper.venue else ""
        print(f"      {year}{venue} · {source}")
        links = paper_web_links(paper)
        if links:
            for label, url in links:
                print(f"      {label}：{url}")
        else:
            print("      网页：当前来源未提供")
    for failure in result.failures:
        ui.warning(f"{failure.source}：{failure.reason}")
        print(f"      应对方案：{failure.suggestion}")
    scholar_query = quote(
        build_google_scholar_query(
            plan.search_query,
            plan.field,
            author_affiliation=author_affiliation,
            author_topic=author_topic,
            author_venue=author_venue,
        ),
        safe="",
    )
    ui.info(f"Google Scholar 手动补充检索：https://scholar.google.com/scholar?q={scholar_query}")

def _show_projects(settings: Settings, ui: TerminalUI) -> None:
    workflow = ResearchWorkflow(settings)
    projects = workflow.store.list_projects(limit=20)
    ui.title("已有项目")
    ui.info(f"数据目录：{settings.home}")
    if not projects:
        ui.info("还没有项目。")
        return
    for project in projects:
        title = project.title or project.idea
        status = STATUS_LABELS.get(project.status, project.status.value)
        print(f"  {project.id}  {status:<6}  {title[:52]}")
    project_id = ui.ask("输入项目 ID 查看制品，直接回车返回")
    if not project_id:
        return
    project = workflow.store.get_project(project_id)
    if project is None:
        ui.error("项目不存在。")
        return
    _show_result(workflow, project, ui)


def _delete_project_wizard(settings: Settings, ui: TerminalUI) -> None:
    workflow = ResearchWorkflow(settings)
    projects = workflow.store.list_projects(limit=20)
    ui.title("删除已有项目")
    ui.info(f"数据目录：{settings.home}")
    if not projects:
        ui.info("还没有项目。")
        return
    for project in projects:
        title = project.title or project.idea
        status = STATUS_LABELS.get(project.status, project.status.value)
        print(f"  {project.id}  {status:<6}  {title[:52]}")
    project_id = ui.ask("输入要删除的项目 ID，直接回车返回")
    if not project_id:
        return
    project = workflow.store.get_project(project_id)
    if project is None:
        ui.error("项目不存在。")
        return
    title = project.title or project.idea
    ui.warning("将永久删除项目记录、事件、上传资料文本和论文制品，不能撤销。")
    if project.status == ProjectStatus.RUNNING:
        ui.warning("该项目上次标记为运行中；系统会检查是否仍有进程正在执行。")
    confirmation = ui.ask(f"输入项目 ID {project.id} 确认删除")
    if confirmation != project.id:
        ui.info("已取消删除。")
        return
    try:
        workflow.store.delete_project(project.id)
    except ProjectBusyError as exc:
        ui.error(f"{exc}，请先停止对应任务后再删除。")
        return
    ui.success(f"已删除：{title[:52]}（{project.id}）")


def _serve(settings: Settings, ui: TerminalUI, host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    from scholaros.api import create_app

    ui.title("网页工作台")
    ui.success(f"启动后访问 http://{host}:{port}")
    ui.info("保持此窗口开启；按 Ctrl+C 停止服务。")
    workflow = ResearchWorkflow(settings)
    uvicorn.run(create_app(workflow), host=host, port=port, log_level="info")


def serve(settings: Settings, host: str, port: int) -> None:
    ui = TerminalUI()
    ui.banner()
    _serve(settings, ui, host, port)


def _ask_paths(ui: TerminalUI, prompt: str) -> list[Path]:
    value = ui.ask(prompt)
    if not value:
        return []
    try:
        candidates = [Path(item).expanduser() for item in shlex.split(value)]
    except ValueError as exc:
        ui.warning(f"路径格式无法解析：{exc}")
        return []
    paths = []
    for path in candidates:
        if path.is_file():
            paths.append(path)
        else:
            ui.warning(f"已跳过不存在的文件：{path}")
    return paths


def _show_result(workflow: ResearchWorkflow, project: Project, ui: TerminalUI) -> None:
    status = STATUS_LABELS.get(project.status, project.status.value)
    ui.title("项目结果")
    ui.info(f"项目 ID：{project.id}")
    ui.info(f"状态：{status}")
    ui.info(f"制品目录：{workflow.settings.artifacts_path / project.id}")
    review = project.state.get("final_review", {})
    if review:
        ui.info(f"质量检查分数：{review.get('score', '—')}/100")
    paper = workflow.store.artifact_path(project.id, "paper.md")
    if paper is not None:
        ui.success(f"论文：{paper}")
    artifacts = workflow.store.list_artifacts(project.id)
    ui.info(f"制品：{', '.join(artifacts) if artifacts else '无'}")
    if project.error:
        ui.error(project.error)
    if project.state.get("pending_checkpoint"):
        ui.info(f"确认继续：scholaros approve {project.id}")
    elif project.state.get("search_confirmation_required"):
        ui.info(f"确认检索：scholaros confirm-search {project.id}")
    elif project.stage != Stage.COMPLETED:
        ui.info(f"从断点继续：scholaros resume {project.id}")
    ui.info(f"局部重做：scholaros rerun {project.id} --from-stage designing")
    ui.info(f"历史版本：scholaros history {project.id}")
