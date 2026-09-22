from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from scholaros.domain import ResearchSpec, utc_now

WORKFLOWS = frozenset(
    {"build_from_materials", "rewrite_existing", "audit", "review", "revise", "transfer"}
)
SCENES = frozenset({"journal", "conference", "report", "review", "competition", "other"})
OUTPUT_LANGUAGES = frozenset({"zh", "en", "multilingual", "other"})
RESEARCH_MODES = frozenset({"agent_decide", "required", "materials_only"})
REQUESTED_SCOPES = frozenset({"manuscript", "local_delivery", "submission_package"})
AUTHOR_VOICE_MODES = frozenset({"off", "standard", "strict"})
REFERENCE_COUNT_MODES = frozenset({"venue_average", "custom"})
MECHANISM_FIGURE_MODES = frozenset({"prefer", "auto", "omit"})
DELIVERY_FORMATS = frozenset({"md", "tex"})
LEGACY_DELIVERY_FORMATS = frozenset({"docx", "pdf"})
DECISION_TYPES = frozenset({"figure"})
FIGURE_DECISIONS = frozenset({"keep", "revise", "omit"})

DEFAULT_CONFIGURATION: dict[str, Any] = {
    "workflow": "build_from_materials",
    "scene": "journal",
    "target_name": "",
    "output_language": "zh",
    "research_mode": "agent_decide",
    "requested_scope": "local_delivery",
    "author_voice": "standard",
    "same_field_papers": 3,
    "target_venue_papers": 3,
    "reference_count_mode": "venue_average",
    "reference_count": None,
    "mechanism_figure": "prefer",
    "formats": ["md", "tex"],
}

SCOPING_CONFIGURATION_FIELDS = frozenset(
    {"workflow", "scene", "target_name", "research_mode"}
)
SEARCH_CONFIGURATION_FIELDS = frozenset(
    {"same_field_papers", "target_venue_papers", "reference_count_mode", "reference_count"}
)
DESIGN_CONFIGURATION_FIELDS = frozenset({"mechanism_figure"})
DRAFT_CONFIGURATION_FIELDS = frozenset({"output_language", "author_voice", "formats"})


def normalize_configuration(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """返回完整、可序列化的工作台配置；未知字段会被拒绝。"""

    raw = dict(value or {})
    unknown = set(raw) - set(DEFAULT_CONFIGURATION)
    if unknown:
        raise ValueError(f"未知项目配置：{', '.join(sorted(unknown))}")
    config = deepcopy(DEFAULT_CONFIGURATION)
    config.update(raw)

    _choice(config, "workflow", WORKFLOWS)
    _choice(config, "scene", SCENES)
    _choice(config, "output_language", OUTPUT_LANGUAGES)
    _choice(config, "research_mode", RESEARCH_MODES)
    _choice(config, "requested_scope", REQUESTED_SCOPES)
    _choice(config, "author_voice", AUTHOR_VOICE_MODES)
    _choice(config, "reference_count_mode", REFERENCE_COUNT_MODES)
    _choice(config, "mechanism_figure", MECHANISM_FIGURE_MODES)

    target_name = str(config.get("target_name") or "").strip()
    if len(target_name) > 200:
        raise ValueError("投稿目标名称不能超过 200 个字符")
    config["target_name"] = target_name

    for field in ("same_field_papers", "target_venue_papers"):
        number = config.get(field)
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= 50:
            raise ValueError(f"{field} 必须是 1—50 的整数")
    count = config.get("reference_count")
    if count is not None and (
        not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 500
    ):
        raise ValueError("reference_count 必须为空或 1—500 的整数")
    if config["reference_count_mode"] == "custom" and count is None:
        raise ValueError("自定义参考文献数量时必须填写 reference_count")

    formats = config.get("formats")
    if not isinstance(formats, Sequence) or isinstance(formats, (str, bytes)):
        raise ValueError("formats 必须是格式名称数组")
    normalized_formats = list(dict.fromkeys(str(item).strip().lower() for item in formats))
    if not normalized_formats:
        raise ValueError("formats 至少选择 md 或 tex 中的一种")
    unknown_formats = set(normalized_formats) - DELIVERY_FORMATS - LEGACY_DELIVERY_FORMATS
    if unknown_formats:
        raise ValueError("formats 只能包含 md、tex，且至少选择一种")
    # 旧项目可能保存过 docx/pdf；读取时平滑迁移，但所有新入口只接受 Markdown/LaTeX。
    # 当前工作台固定同时保留 Markdown 与 LaTeX；旧项目中的单格式或历史格式在读取时迁移。
    config["formats"] = list(DEFAULT_CONFIGURATION["formats"])
    return config


def configuration_changes(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> set[str]:
    before = normalize_configuration(previous)
    after = normalize_configuration(current)
    return {key for key in DEFAULT_CONFIGURATION if before[key] != after[key]}


def configuration_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    value = normalize_configuration(config)
    return {
        "work_type": value["workflow"],
        "target": value["target_name"] or "venue-neutral",
        "language": value["output_language"],
        "research_boundary": value["research_mode"],
        "learning_set": value["same_field_papers"] + value["target_venue_papers"],
        "formats": value["formats"],
    }


def build_learning_plan(config: Mapping[str, Any], spec: ResearchSpec) -> dict[str, Any]:
    value = normalize_configuration(config)
    target = value["target_name"]
    if value["research_mode"] == "materials_only":
        return {
            "same_field_target": 0,
            "target_venue_target": 0,
            "target_name": target or None,
            "reference_count_mode": value["reference_count_mode"],
            "reference_count": value["reference_count"],
            "topic_queries": [],
            "venue_query": None,
            "limits": [
                "已选择只使用本地材料与结果；工作流不会向第三方论文源发送检索请求。",
                "本地材料的出版状态、许可与引文信息仍需研究者确认。",
            ],
        }
    return {
        "same_field_target": value["same_field_papers"],
        "target_venue_target": value["target_venue_papers"] if target else 0,
        "target_name": target or None,
        "reference_count_mode": value["reference_count_mode"],
        "reference_count": value["reference_count"],
        "topic_queries": list(spec.keywords[:6]),
        "venue_query": f"{spec.keywords[0]} {target}" if target and spec.keywords else None,
        "limits": [
            "检索条目是候选学习集，摘要不能证明已经完成全文或版式学习。",
            "目标期刊规则和模板需要研究者按当前官方说明复核。",
        ],
    }


def build_contribution_blueprint(spec: ResearchSpec) -> dict[str, Any]:
    """形成一份可直接修改的贡献蓝图，不再要求从候选方向中选择。"""

    return {
        "research_question": spec.question,
        "intended_contribution": spec.contribution,
        "boundaries": [
            "所有核心结论仍需由真实结果、对照与敏感性分析验证。",
            "明确区分已观察事实、已有知识与仍待验证的解释路径。",
            "主动报告失败条件、适用范围和无法由当前材料支持的主张。",
        ],
        "framework": [
            "把研究问题拆成可证伪假设、主要指标与对照条件。",
            "以证据账本约束每个背景判断和方法选择。",
            "按研究设计、真实结果、稳健性检查和局限性组织论证。",
        ],
    }


def build_figure_story(
    config: Mapping[str, Any], *, has_results: bool, design: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    value = normalize_configuration(config)
    design = dict(design or {})
    question = str(design.get("research_question") or "已确认的研究问题")
    hypotheses = [str(item) for item in design.get("hypotheses", [])[:3]]
    baselines = [str(item) for item in design.get("baselines", [])[:3]]
    figures: list[dict[str, Any]] = [
        {
            "id": "figure-1",
            "title": "研究逻辑与证据链",
            "job": "解释研究问题、证据、方法与可证伪结论之间的关系。",
            "composition": [
                f"研究问题：{question}",
                "左侧为证据来源与纳入边界，中部为研究步骤，右侧为可证伪结论与失败条件。",
                *([f"假设节点：{item}" for item in hypotheses] or ["假设节点：待按研究问题确认。"]),
            ],
            "visual_encoding": [
                "实线表示有证据支持的流程，虚线表示待验证推断。",
                "颜色只区分证据、方法、结果三类，不编码未经验证的强弱关系。",
            ],
            "data_requirements": "使用研究规格、证据账本和方法设计即可绘制，不需要伪造实验数值。",
            "caption": "图 1. 从研究问题到证据、方法与可证伪结论的整体研究框架。",
            "boundary": "图中不得把计划中的分析或假设标成已观察结果。",
            "kind": "framework",
            "status": "planned",
            "media": None,
            "decision": None,
            "comment": "",
        },
        {
            "id": "figure-2",
            "title": "主要发现与对照",
            "job": "展示主要指标、对照、区间和关键稳健性结果。",
            "composition": [
                "主面板呈现主要指标及置信区间；副面板呈现敏感性分析与失败案例。",
                *([f"对照：{item}" for item in baselines] or ["对照：待在真实结果中确认。"]),
            ],
            "visual_encoding": [
                "点或柱表示估计值，误差线表示区间；不同对照保持统一颜色映射。",
                "缺失或尚未完成的结果明确标为待补，不使用示意数值。",
            ],
            "data_requirements": (
                "从上传的真实结果材料提取主要指标、区间和稳健性结果。"
                if has_results
                else "等待真实结果材料；当前仅保留版式、字段和图注规划。"
            ),
            "caption": "图 2. 主要结果、对照与稳健性分析（数值以最终核验结果为准）。",
            "boundary": "没有真实结果时不得绘制数值型结果图。",
            "kind": "results",
            "status": "planned" if has_results else "waiting_for_results",
            "media": None,
            "decision": None,
            "comment": "",
        },
    ]
    if value["mechanism_figure"] != "omit":
        figures.append(
            {
                "id": "figure-3",
                "title": "机制、方法或系统架构",
                "job": "区分已观察事实、已有知识与仍待验证的解释路径。",
                "composition": [
                    "按输入、处理模块、输出和验证反馈四层组织机制或系统组件。",
                    "每个组件标注输入输出、关键假设及其证据来源。",
                ],
                "visual_encoding": [
                    "模块使用统一矩形节点，数据流使用箭头，反馈或迭代使用回环箭头。",
                    "待验证机制使用虚线边框，并在图例中单独说明。",
                ],
                "data_requirements": "以研究设计中的变量、分析步骤和失败条件为准。",
                "caption": "图 3. 方法、机制或系统架构及其验证接口。",
                "boundary": "架构关系是研究设计，不代表机制已经得到因果验证。",
                "kind": "mechanism",
                "status": "planned",
                "media": None,
                "decision": None,
                "comment": "",
            }
        )
    return figures


def build_table_story(
    config: Mapping[str, Any], *, has_results: bool, design: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """规划可直接交给研究者填充的表格，不生成虚构结果。"""

    normalize_configuration(config)
    design = dict(design or {})
    variables = [
        *[str(item) for item in design.get("independent_variables", [])[:3]],
        *[str(item) for item in design.get("dependent_variables", [])[:3]],
    ]
    tables = [
        {
            "id": "table-1",
            "title": "证据纳入与可追踪性清单",
            "purpose": "逐条连接研究主张、论文或本地材料、证据定位与核验状态。",
            "columns": ["主张/问题", "来源", "证据摘要", "定位链接或文件", "限制", "人工核验状态"],
            "rows": "每条证据账本记录一行；同一主张由多条证据支持时分行记录。",
            "data_requirements": "来自 evidence.json、papers.json 与上传材料清单。",
            "caption": "表 1. 研究主张与证据来源的可追踪性矩阵。",
            "boundary": "摘要级证据不得标为已完成全文核验。",
        },
        {
            "id": "table-2",
            "title": "变量、指标与分析计划",
            "purpose": "把研究变量、主要指标、对照、统计分析与失败条件放在同一核验表中。",
            "columns": ["变量/指标", "操作定义", "数据来源", "对照", "分析方法", "失败或停止条件"],
            "rows": variables or ["按研究设计逐项填写变量与指标，不预填虚构数值。"],
            "data_requirements": "来自 research-design.json；执行研究后补充最终数据定位。",
            "caption": "表 2. 变量、主要指标与预先规定的分析方案。",
            "boundary": "计划与完成结果必须分栏记录，不能用预期结果替代观测结果。",
        },
    ]
    if has_results:
        tables.append(
            {
                "id": "table-3",
                "title": "主要结果与稳健性检查",
                "purpose": "汇总真实结果、区间、对照差异、敏感性分析和异常情况。",
                "columns": ["分析", "样本/切分", "估计值", "区间", "对照", "稳健性", "材料定位"],
                "rows": "只从 results 角色材料中提取；无法定位的字段保留为空并标记待核验。",
                "data_requirements": "来自用户上传的真实结果文件。",
                "caption": "表 3. 主要结果、对照与稳健性检查汇总。",
                "boundary": "禁止推算、补齐或编造上传材料中不存在的结果。",
            }
        )
    return tables


def record_decision(
    existing: Sequence[Mapping[str, Any]],
    *,
    decision_type: str,
    item_id: str,
    value: str,
    comment: str = "",
) -> list[dict[str, Any]]:
    if decision_type not in DECISION_TYPES:
        raise ValueError("decision_type 只能是 figure")
    clean_item = item_id.strip()
    clean_value = value.strip()
    if not clean_item or len(clean_item) > 100:
        raise ValueError("选择项 ID 无效")
    if decision_type == "figure" and clean_value not in FIGURE_DECISIONS:
        raise ValueError("图件选择只能是 keep、revise 或 omit")
    if not clean_value or len(clean_value) > 200:
        raise ValueError("选择值无效")
    clean_comment = comment.strip()
    if len(clean_comment) > 5000:
        raise ValueError("选择意见不能超过 5000 个字符")
    result = [dict(item) for item in existing]
    result = [
        item
        for item in result
        if not (item.get("type") == decision_type and item.get("item_id") == clean_item)
    ]
    result.append(
        {
            "type": decision_type,
            "item_id": clean_item,
            "value": clean_value,
            "comment": clean_comment,
            "recorded_at": utc_now(),
        }
    )
    return result


def _choice(config: Mapping[str, Any], field: str, allowed: frozenset[str]) -> None:
    if config.get(field) not in allowed:
        raise ValueError(f"{field} 必须是：{', '.join(sorted(allowed))}")
