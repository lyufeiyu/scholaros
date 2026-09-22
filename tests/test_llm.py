from __future__ import annotations

import json
import traceback
from dataclasses import replace
from typing import Any

import httpx
import pytest

from scholaros.llm import OpenAICompatibleModel, _safe_api_base
from scholaros.runtime import AgentLoop, AgentMessage, ToolRegistry


def model_with(settings, handler) -> OpenAICompatibleModel:
    """用 MockTransport 构造一个不发真实网络请求的模型。"""

    return OpenAICompatibleModel(settings, transport=httpx.MockTransport(handler))


def test_safe_api_base_hides_url_credentials() -> None:
    assert _safe_api_base("https://user:secret@example.com/v1?token=hidden") == (
        "https://example.com/v1"
    )


@pytest.mark.asyncio
async def test_model_waits_for_non_streaming_full_response(settings, monkeypatch) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        captured["auth"] = request.headers["Authorization"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "完整回答", "reasoning_content": "推理"}}]},
        )

    result = await model_with(configured, handler).turn(
        [AgentMessage(role="user", content="回答")], []
    )

    assert captured["auth"] == "Bearer secret"
    assert captured["payload"]["stream"] is False
    assert "thinking" not in captured["payload"]
    assert result.content == "完整回答"
    assert result.reasoning_content == "推理"
    completed_message = OpenAICompatibleModel._convert_message(
        AgentMessage(role="assistant", content="完整回答", reasoning_content="不应回传")
    )
    assert "reasoning_content" not in completed_message


@pytest.mark.asyncio
async def test_thinking_content_is_replayed_after_tool_call(settings, monkeypatch) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(
        settings,
        api_key_env="SCHOLAROS_TEST_KEY",
        model_timeout=420,
        model_thinking="enabled",
    )
    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "先调用工具核验",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "echo",
                                        "arguments": '{"value": "已核验"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {"choices": [{"message": {"content": "最终回答"}}]},
        ]
    )
    payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    tools = ToolRegistry()
    tools.register(
        "echo",
        "回显",
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        lambda value: value,
    )
    loop = AgentLoop(model_with(configured, handler), tools)

    assert await loop.run("核验后回答", allowed_tools={"echo"}) == "最终回答"
    assert payloads[0]["thinking"] == {"type": "enabled"}
    assistant_message = next(
        item for item in payloads[1]["messages"] if item["role"] == "assistant"
    )
    assert assistant_message["reasoning_content"] == "先调用工具核验"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda request: httpx.ReadTimeout("read timed out", request=request),
        lambda request: httpx.ConnectTimeout("connect timed out", request=request),
    ],
)
async def test_model_timeout_has_actionable_chinese_error(
    settings, monkeypatch, exc_factory
) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_factory(request)

    with pytest.raises(RuntimeError, match="300 秒.*SCHOLAROS_MODEL_TIMEOUT_SECONDS"):
        await model_with(configured, handler).turn(
            [AgentMessage(role="user", content="回答")], []
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "public_message"),
    [
        (
            httpx.Response(
                401, content=b"Authorization: Bearer actual-review-secret"
            ),
            "拒绝了当前 API Key",
        ),
        (
            None,
            "无法连接模型服务",
        ),
    ],
)
async def test_model_errors_do_not_expose_upstream_secrets(
    settings, monkeypatch, response, public_message: str
) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        if response is not None:
            return response
        raise httpx.ConnectError("proxy password=actual-review-secret", request=request)

    with pytest.raises(RuntimeError) as captured:
        await model_with(configured, handler).turn(
            [AgentMessage(role="user", content="回答")], []
        )

    message = "".join(traceback.format_exception(captured.value))
    assert public_message in message
    assert "actual-review-secret" not in message
    assert "Authorization" not in message
    assert "password" not in message


@pytest.mark.asyncio
async def test_model_response_read_is_bounded(settings, monkeypatch) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    monkeypatch.setattr("scholaros.llm.MAX_MODEL_RESPONSE_BYTES", 10)
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"x" * 128)

    with pytest.raises(RuntimeError, match="32 MiB 安全上限"):
        await model_with(configured, handler).turn(
            [AgentMessage(role="user", content="回答")], []
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"error": {"message": "secret"}}, {"choices": []}])
async def test_model_200_error_envelope_has_clear_fixed_error(
    settings, monkeypatch, body: dict[str, Any]
) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=body)

    with pytest.raises(RuntimeError, match="未返回可用回答") as captured:
        await model_with(configured, handler).turn(
            [AgentMessage(role="user", content="回答")], []
        )

    assert "secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_null_tool_calls_are_treated_as_no_tool_calls(settings, monkeypatch) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "完整回答", "tool_calls": None}}]},
        )

    result = await model_with(configured, handler).turn(
        [AgentMessage(role="user", content="回答")], []
    )

    assert result.content == "完整回答"
    assert result.tool_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_calls", [{}, "", 0])
async def test_falsy_non_list_tool_calls_are_rejected(
    settings, monkeypatch, tool_calls: object
) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "", "tool_calls": tool_calls}}]},
        )

    with pytest.raises(RuntimeError, match="工具调用结构无效"):
        await model_with(configured, handler).turn(
            [AgentMessage(role="user", content="回答")], []
        )


@pytest.mark.asyncio
async def test_model_ignores_socks_proxy_env(settings, monkeypatch) -> None:
    """用户环境可能设置 ALL_PROXY=socks5://…，模型请求应直连而不是读 socks 代理。"""

    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9999")
    configured = replace(
        settings,
        api_key_env="SCHOLAROS_TEST_KEY",
        api_base="https://127.0.0.1:9",
        model_timeout=2,
    )

    with pytest.raises(RuntimeError, match="无法连接模型服务"):
        await OpenAICompatibleModel(configured).turn(
            [AgentMessage(role="user", content="回答")], []
        )
