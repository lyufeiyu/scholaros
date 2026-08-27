from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class AgentMessage:
    """领域消息只在模型边界转换，便于保留来源、角色和工具语义。"""

    role: str
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    reasoning_content: str = ""


@dataclass(slots=True)
class ModelTurn:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str = ""


class TurnModel(Protocol):
    async def turn(
        self, messages: Sequence[AgentMessage], tools: list[dict[str, Any]]
    ) -> ModelTurn: ...


ToolHandler = Callable[..., Any] | Callable[..., Awaitable[Any]]
EventSink = Callable[[str, dict[str, Any]], None | Awaitable[None]]


@dataclass(slots=True)
class RegisteredTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: ToolHandler,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"工具已注册：{name}")
        self._tools[name] = RegisteredTool(name, description, parameters, handler)

    def schemas(self, allowed: set[str] | None = None) -> list[dict[str, Any]]:
        names = allowed if allowed is not None else set(self._tools)
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for name, tool in self._tools.items()
            if name in names
        ]

    async def execute(self, call: ToolCall, allowed: set[str] | None = None) -> Any:
        if allowed is not None and call.name not in allowed:
            raise PermissionError(f"当前 Agent 不允许调用工具：{call.name}")
        tool = self._tools.get(call.name)
        if tool is None:
            raise KeyError(f"未知工具：{call.name}")
        self._validate_arguments(tool.parameters, call.arguments)
        result = tool.handler(**call.arguments)
        return await result if inspect.isawaitable(result) else result

    @staticmethod
    def _validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
        required = schema.get("required", [])
        missing = [key for key in required if key not in arguments]
        if missing:
            raise ValueError(f"缺少工具参数：{', '.join(missing)}")
        properties = schema.get("properties", {})
        extra = set(arguments) - set(properties)
        if properties and extra:
            raise ValueError(f"未声明的工具参数：{', '.join(sorted(extra))}")


class AgentLoop:
    """事件化循环，支持工具调用、steering 与 follow-up。"""

    def __init__(
        self,
        model: TurnModel,
        tools: ToolRegistry | None = None,
        event_sink: EventSink | None = None,
        max_turns: int = 8,
    ) -> None:
        self.model = model
        self.tools = tools or ToolRegistry()
        self.event_sink = event_sink
        self.max_turns = max_turns
        self.messages: list[AgentMessage] = []
        self._steering: asyncio.Queue[AgentMessage] = asyncio.Queue()
        self._follow_up: asyncio.Queue[AgentMessage] = asyncio.Queue()

    async def steer(self, content: str) -> None:
        await self._steering.put(
            AgentMessage(role="user", content=content, metadata={"steering": True})
        )

    async def follow_up(self, content: str) -> None:
        await self._follow_up.put(
            AgentMessage(role="user", content=content, metadata={"follow_up": True})
        )

    async def run(
        self,
        prompt: str,
        *,
        system: str = "",
        allowed_tools: set[str] | None = None,
    ) -> str:
        if system and not self.messages:
            self.messages.append(AgentMessage(role="system", content=system))
        self.messages.append(AgentMessage(role="user", content=prompt))
        await self._emit("message_end", {"role": "user", "content": prompt})

        final_text = ""
        for turn_index in range(self.max_turns):
            await self._drain_queue(self._steering)
            await self._emit("turn_start", {"index": turn_index})
            result = await self.model.turn(self.messages, self.tools.schemas(allowed_tools))
            assistant = AgentMessage(
                role="assistant",
                content=result.content,
                tool_calls=result.tool_calls,
                reasoning_content=result.reasoning_content,
            )
            self.messages.append(assistant)
            await self._emit(
                "message_end",
                {"role": "assistant", "content": result.content, "tools": len(result.tool_calls)},
            )
            if not result.tool_calls:
                final_text = result.content
                if self._follow_up.empty():
                    await self._emit("agent_end", {"turns": turn_index + 1})
                    return final_text
                await self._drain_queue(self._follow_up)
                continue

            for call in result.tool_calls:
                await self._emit("tool_start", {"id": call.id, "name": call.name})
                try:
                    value = await self.tools.execute(call, allowed_tools)
                    content = json.dumps(value, ensure_ascii=False, default=str)
                    is_error = False
                except Exception as exc:  # 工具失败应回到模型，而不是炸掉整个会话。
                    content = f"{type(exc).__name__}: {exc}"
                    is_error = True
                self.messages.append(
                    AgentMessage(
                        role="tool",
                        name=call.name,
                        tool_call_id=call.id,
                        content=content,
                        metadata={"is_error": is_error},
                    )
                )
                await self._emit(
                    "tool_end", {"id": call.id, "name": call.name, "is_error": is_error}
                )

        raise RuntimeError(f"Agent 超过最大轮数 {self.max_turns}")

    async def _drain_queue(self, queue: asyncio.Queue[AgentMessage]) -> None:
        while not queue.empty():
            message = queue.get_nowait()
            self.messages.append(message)
            await self._emit(
                "message_end",
                {"role": message.role, "content": message.content, **message.metadata},
            )

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        result = self.event_sink(event_type, payload)
        if inspect.isawaitable(result):
            await result
