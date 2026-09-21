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
DELIVERY_FORMATS = frozenset({"md", "docx", "tex", "pdf"})
DECISION_TYPES = frozenset({"contribution", "figure"})
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
    "formats": ["md", "docx", "tex", "pdf"],
}

SCOPING_CONFIGURATION_FIELDS = frozenset(
    {"workflow", "scene", "target_name", "research_mode"}
)
SEARCH_CONFIGURATION_FIELDS = frozenset(
    {"same_field_papers", "target_venue_papers", "reference_count_mode", "reference_count"}
)
DESIGN_CONFIGURATION_FIELDS = frozenset({"mechanism_figure"})
DRAFT_CONFIGURATION_FIELDS = frozenset({"output_language", "author_voice"})


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
    if not normalized_formats or set(normalized_formats) - DELIVERY_FORMATS:
        raise ValueError("formats 只能包含 md、docx、tex、pdf，且至少选择一种")
    config["formats"] = normalized_formats
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


def build_contribution_options(spec: ResearchSpec) -> list[dict[str, str]]:
    """给出有边界的方向选择，不声称已经得到研究结果。"""

    return [
        {
            "id": "primary",
            "title": "核心研究贡献",
            "summary": spec.contribution,
            "tradeoff": "最贴近当前研究问题，仍需由真实结果验证。",
        },
        {
            "id": "validation",
            "title": "验证与适用边界",
            "summary": "系统检验当前研究问题在不同条件下的有效性、稳健性与失败边界。",
            "tradeoff": "结论更审慎，需要更完整的对照、敏感性分析与反例。",
        },
        {
            "id": "methodology",
            "title": "方法与可复现性",
            "summary": "把研究流程、证据约束和分析步骤操作化为可复核、可复现的方法框架。",
            "tradeoff": "方法贡献更强，但不能代替对核心科学问题的实证回答。",
        },
    ]


def build_figure_story(
    config: Mapping[str, Any], *, has_results: bool
) -> list[dict[str, Any]]:
    value = normalize_configuration(config)
    figures: list[dict[str, Any]] = [
        {
            "id": "figure-1",
            "title": "研究逻辑与证据链",
            "job": "解释研究问题、证据、方法与可证伪结论之间的关系。",
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
                "kind": "mechanism",
                "status": "planned",
                "media": None,
                "decision": None,
                "comment": "",
            }
        )
    return figures


def record_decision(
    existing: Sequence[Mapping[str, Any]],
    *,
    decision_type: str,
    item_id: str,
    value: str,
    comment: str = "",
) -> list[dict[str, Any]]:
    if decision_type not in DECISION_TYPES:
        raise ValueError("decision_type 只能是 contribution 或 figure")
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
        if not (
            item.get("type") == decision_type
            and (
                item.get("item_id") == clean_item
                or decision_type == "contribution"
            )
        )
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
