from __future__ import annotations

from pathlib import Path

import pytest

from scholaros.prompt_skills import ROLE_SKILLS, skill_instructions
from scholaros.runtime import ModelTurn
from scholaros.writing import ResearchWriter


@pytest.mark.asyncio
async def test_every_model_role_receives_only_relevant_skill_instructions() -> None:
    class RecordingModel:
        def __init__(self) -> None:
            self.messages = []

        async def turn(self, messages, tools):
            self.messages = list(messages)
            return ModelTurn("ok")

    assert len(ROLE_SKILLS) == 6
    for role, selected in ROLE_SKILLS.items():
        model = RecordingModel()
        assert await ResearchWriter(model)._ask(role, "用户任务") == "ok"
        system, user = model.messages[:2]
        assert system.role == "system" and user.content == "用户任务"
        for name in selected:
            assert name in {path.parent.name for path in Path("skills").glob("*/SKILL.md")}
        assert skill_instructions(role) in system.content
        assert len(skill_instructions(role)) < 1000
    assert "模型记忆" in skill_instructions("论文发现 Agent")
    assert "引文" in skill_instructions("修订辅助 Agent")
    assert "引文" not in skill_instructions("研究规划 Agent")


def test_project_skill_files_match_packaged_runtime_copies() -> None:
    root = Path(__file__).resolve().parents[1]
    names = {path.parent.name for path in (root / "skills").glob("*/SKILL.md")}
    assert names == {name for group in ROLE_SKILLS.values() for name in group}
    for name in names:
        source = (root / "skills" / name / "SKILL.md").read_bytes()
        packaged = (root / "src" / "scholaros" / "skills" / name / "SKILL.md").read_bytes()
        assert source == packaged, name
        assert b"model-instructions:start" in source


@pytest.mark.asyncio
async def test_ieee_english_rewrite_preserves_full_manuscript_request() -> None:
    class RecordingModel:
        def __init__(self) -> None:
            self.messages = []

        async def turn(self, messages, tools):
            self.messages = list(messages)
            return ModelTurn("# English Title\n\n## Abstract\nEnglish abstract.\n\n## Keywords\nprotocol\n\n## Introduction\nText.\n\n## Conclusion\nEnd.\n\n## References\n")

    model = RecordingModel()
    revised = await ResearchWriter(model).ensure_ieee_english("# 中文标题\n\n## 摘要\n中文摘要")
    assert revised.startswith("# English Title")
    assert "Translate every heading and prose paragraph" in model.messages[1].content
    assert "and References" in model.messages[1].content


@pytest.mark.asyncio
async def test_ieee_rewrite_rejects_lost_citation_keys() -> None:
    class DroppingModel:
        async def turn(self, messages, tools):
            return ModelTurn("# English Title\n\n## Abstract\nThe source was omitted.")

    with pytest.raises(ValueError, match="丢失原稿引用键"):
        await ResearchWriter(DroppingModel()).ensure_ieee_english(
            "# 中文标题\n\n## 引言\n证据 [@Source1]。"
        )
