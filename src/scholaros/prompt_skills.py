from __future__ import annotations

from functools import cache
from importlib.resources import files

MODEL_START = "<!-- model-instructions:start -->"
MODEL_END = "<!-- model-instructions:end -->"

ROLE_SKILLS: dict[str, tuple[str, ...]] = {
    "研究规划 Agent": ("research-scoping",),
    "论文检索规划 Agent": ("literature-discovery",),
    "论文发现 Agent": ("literature-discovery",),
    "研究方法 Agent": ("research-method-design", "evidence-ledger"),
    "研究写作辅助 Agent": ("manuscript-writing", "evidence-ledger"),
    "修订辅助 Agent": ("manuscript-writing", "evidence-ledger", "manuscript-evidence-gate"),
}


@cache
def _model_instructions(name: str) -> str:
    resource = files("scholaros").joinpath("skills", name, "SKILL.md")
    source = resource.read_text(encoding="utf-8")
    if source.count(MODEL_START) != 1 or source.count(MODEL_END) != 1:
        raise ValueError(f"技能 {name} 缺少唯一的模型约束片段")
    instructions = source.split(MODEL_START, 1)[1].split(MODEL_END, 1)[0].strip()
    if not instructions:
        raise ValueError(f"技能 {name} 的模型约束不能为空")
    return instructions


def skill_instructions(role: str) -> str:
    """仅装载当前角色需要的技能约束，不把整份 Agent 手册塞入模型。"""

    return "\n".join(_model_instructions(name) for name in ROLE_SKILLS[role])
