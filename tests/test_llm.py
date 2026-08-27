from __future__ import annotations

import io
import json
import traceback
import urllib.error
from dataclasses import replace
from typing import Any

import pytest

from scholaros.llm import OpenAICompatibleModel, _safe_api_base
from scholaros.runtime import AgentLoop, AgentMessage, ToolRegistry


class FakeResponse:
    def __init__(self, value: dict[str, Any]):
        self.raw = json.dumps(value).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return self.raw


def test_safe_api_base_hides_url_credentials() -> None:
    assert _safe_api_base("https://user:secret@example.com/v1?token=hidden") == (
        "https://example.com/v1"
    )


@pytest.mark.asyncio
async def test_model_waits_for_non_streaming_full_response(settings, monkeypatch) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")
    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse(
            {"choices": [{"message": {"content": "完整回答", "reasoning_content": "推理"}}]}
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = await OpenAICompatibleModel(configured).turn(
        [AgentMessage(role="user", content="回答")], []
    )

    assert captured["timeout"] == 300
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

    def fake_urlopen(request, timeout):
        assert timeout == 420
        payloads.append(json.loads(request.data))
        return FakeResponse(next(responses))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
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
    loop = AgentLoop(OpenAICompatibleModel(configured), tools)

    assert await loop.run("核验后回答", allowed_tools={"echo"}) == "最终回答"
    assert payloads[0]["thinking"] == {"type": "enabled"}
    assistant_message = next(
        item for item in payloads[1]["messages"] if item["role"] == "assistant"
    )
    assert assistant_message["reasoning_content"] == "先调用工具核验"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [TimeoutError("read timed out"), urllib.error.URLError(TimeoutError("read timed out"))],
)
async def test_model_timeout_has_actionable_chinese_error(
    settings, monkeypatch, error: Exception
) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")

    def fake_urlopen(_request, timeout):
        assert timeout == 300
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="300 秒.*SCHOLAROS_MODEL_TIMEOUT_SECONDS"):
        await OpenAICompatibleModel(configured).turn(
            [AgentMessage(role="user", content="回答")], []
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "public_message"),
    [
        (
            urllib.error.HTTPError(
                "https://example.invalid/chat/completions",
                401,
                "unauthorized",
                {},
                io.BytesIO(b"Authorization: Bearer actual-review-secret"),
            ),
            "拒绝了当前 API Key",
        ),
        (
            urllib.error.URLError("proxy password=actual-review-secret"),
            "无法连接模型服务",
        ),
    ],
)
async def test_model_errors_do_not_expose_upstream_secrets(
    settings, monkeypatch, error: Exception, public_message: str
) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")

    def fake_urlopen(_request, timeout):
        del timeout
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as captured:
        await OpenAICompatibleModel(configured).turn(
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

    class OversizedResponse(FakeResponse):
        def __init__(self):
            super().__init__({})
            self.requested_size = 0

        def read(self, size: int = -1) -> bytes:
            self.requested_size = size
            return b"x" * size

    response = OversizedResponse()

    def fake_urlopen(_request, timeout):
        del timeout
        return response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="32 MiB 安全上限"):
        await OpenAICompatibleModel(configured).turn(
            [AgentMessage(role="user", content="回答")], []
        )

    assert response.requested_size == 11


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"error": {"message": "secret"}}, {"choices": []}])
async def test_model_200_error_envelope_has_clear_fixed_error(
    settings, monkeypatch, body: dict[str, Any]
) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda _request, timeout: FakeResponse(body)
    )

    with pytest.raises(RuntimeError, match="未返回可用回答") as captured:
        await OpenAICompatibleModel(configured).turn(
            [AgentMessage(role="user", content="回答")], []
        )

    assert "secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_null_tool_calls_are_treated_as_no_tool_calls(settings, monkeypatch) -> None:
    monkeypatch.setenv("SCHOLAROS_TEST_KEY", "secret")
    configured = replace(settings, api_key_env="SCHOLAROS_TEST_KEY")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda _request, timeout: FakeResponse(
            {"choices": [{"message": {"content": "完整回答", "tool_calls": None}}]}
        ),
    )

    result = await OpenAICompatibleModel(configured).turn(
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
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda _request, timeout: FakeResponse(
            {"choices": [{"message": {"content": "", "tool_calls": tool_calls}}]}
        ),
    )

    with pytest.raises(RuntimeError, match="工具调用结构无效"):
        await OpenAICompatibleModel(configured).turn(
            [AgentMessage(role="user", content="回答")], []
        )
