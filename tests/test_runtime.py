from __future__ import annotations

import pytest

from scholaros.llm import ScriptedModel
from scholaros.runtime import AgentLoop, ModelTurn, ToolCall, ToolRegistry


@pytest.mark.asyncio
async def test_agent_loop_executes_tool_and_emits_events() -> None:
    model = ScriptedModel(
        [
            ModelTurn("", [ToolCall("1", "add", {"left": 2, "right": 3})]),
            ModelTurn("结果是 5"),
        ]
    )
    registry = ToolRegistry()
    registry.register(
        "add",
        "求和",
        {
            "type": "object",
            "properties": {"left": {"type": "number"}, "right": {"type": "number"}},
            "required": ["left", "right"],
        },
        lambda left, right: left + right,
    )
    events: list[str] = []
    loop = AgentLoop(model, registry, lambda name, payload: events.append(name))

    assert await loop.run("计算", allowed_tools={"add"}) == "结果是 5"
    assert "tool_start" in events
    assert "tool_end" in events
    assert loop.messages[-2].role == "tool"
    assert loop.messages[-2].content == "5"


@pytest.mark.asyncio
async def test_tool_errors_return_to_model() -> None:
    model = ScriptedModel(
        [
            ModelTurn("", [ToolCall("1", "safe", {})]),
            ModelTurn("参数错误已处理"),
        ]
    )
    registry = ToolRegistry()
    registry.register(
        "safe",
        "需要参数",
        {"type": "object", "properties": {"value": {}}, "required": ["value"]},
        lambda value: value,
    )
    loop = AgentLoop(model, registry)

    assert await loop.run("调用") == "参数错误已处理"
    tool_message = next(message for message in loop.messages if message.role == "tool")
    assert tool_message.metadata["is_error"] is True
    assert "缺少工具参数" in tool_message.content
