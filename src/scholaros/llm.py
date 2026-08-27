from __future__ import annotations

import asyncio
import contextlib
import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from scholaros.config import Settings
from scholaros.runtime import AgentMessage, ModelTurn, ToolCall

MAX_MODEL_RESPONSE_BYTES = 32 * 1024 * 1024


class OpenAICompatibleModel:
    """最小 OpenAI-compatible Chat Completions 适配器。"""

    def __init__(self, settings: Settings):
        if not settings.api_key:
            raise ValueError(f"未设置模型密钥环境变量：{settings.api_key_env}")
        self.settings = settings

    async def turn(
        self, messages: Sequence[AgentMessage], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [self._convert_message(message) for message in messages],
            "temperature": 0.2,
            "stream": False,
        }
        if self.settings.model_thinking != "auto":
            payload["thinking"] = {"type": self.settings.model_thinking}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        body = await asyncio.to_thread(self._post, payload)
        choice = _response_message(body)
        raw_calls = choice.get("tool_calls")
        if raw_calls is None:
            raw_calls = []
        elif not isinstance(raw_calls, list):
            raise RuntimeError("模型服务返回的工具调用结构无效")
        calls = []
        for item in raw_calls:
            if not isinstance(item, dict) or not isinstance(item.get("function"), dict):
                raise RuntimeError("模型服务返回的工具调用结构无效")
            function = item["function"]
            raw_arguments = function.get("arguments", "{}")
            if isinstance(raw_arguments, dict):
                arguments = raw_arguments
            elif isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {}
            else:
                arguments = {}
            calls.append(
                ToolCall(
                    id=item.get("id", "tool-call"),
                    name=function.get("name", ""),
                    arguments=arguments,
                )
            )
        content = choice.get("content")
        reasoning_content = choice.get("reasoning_content")
        if content is not None and not isinstance(content, str):
            raise RuntimeError("模型服务返回的文本内容结构无效")
        if reasoning_content is not None and not isinstance(reasoning_content, str):
            raise RuntimeError("模型服务返回的深度思考内容结构无效")
        return ModelTurn(
            content=content or "",
            tool_calls=calls,
            reasoning_content=reasoning_content or "",
        )

    def _post(self, payload: dict[str, Any]) -> Any:
        url = f"{self.settings.api_base}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "ScholarOS/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.model_timeout) as response:
                raw = response.read(MAX_MODEL_RESPONSE_BYTES + 1)
                if len(raw) > MAX_MODEL_RESPONSE_BYTES:
                    raise RuntimeError("模型服务响应超过 32 MiB 安全上限，已停止读取")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("模型服务返回了无法解析的 JSON 响应") from exc
        except urllib.error.HTTPError as exc:
            with contextlib.suppress(OSError):
                exc.close()
            raise RuntimeError(
                f"{_http_error_message(exc.code)}；"
                f"当前模型={self.settings.model}，API Base={_safe_api_base(self.settings.api_base)}，"
                f"密钥变量={self.settings.api_key_env}"
            ) from None
        except TimeoutError:
            raise RuntimeError(
                f"模型服务在 {self.settings.model_timeout:g} 秒内未完成响应；"
                "可稍后重试，或在 .env 调大 SCHOLAROS_MODEL_TIMEOUT_SECONDS"
            ) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise RuntimeError(
                    f"模型服务在 {self.settings.model_timeout:g} 秒内未完成响应；"
                    "可稍后重试，或在 .env 调大 SCHOLAROS_MODEL_TIMEOUT_SECONDS"
                ) from None
            raise RuntimeError(
                "无法连接模型服务；请检查 SCHOLAROS_API_BASE、DNS、代理和 TLS 配置"
            ) from None

    @staticmethod
    def _convert_message(message: AgentMessage) -> dict[str, Any]:
        value: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.name:
            value["name"] = message.name
        if message.tool_call_id:
            value["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            value["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        if message.role == "assistant" and message.tool_calls and message.reasoning_content:
            value["reasoning_content"] = message.reasoning_content
        return value


def _http_error_message(status_code: int) -> str:
    if status_code == 401:
        return (
            "模型服务拒绝了当前 API Key（HTTP 401）；"
            "请在供应商后台确认密钥仍有效，更新 .env 后完全重启 ScholarOS"
        )
    if status_code == 403:
        return "当前账号无权访问该模型（HTTP 403）；请检查模型权限、账户状态和 API Base"
    if status_code == 402:
        return "模型服务账户额度不足（HTTP 402）；请检查供应商余额或账单状态"
    if status_code == 429:
        return "模型服务请求过于频繁（HTTP 429）；请等待配额恢复后重试"
    if status_code >= 500:
        return f"模型服务暂时不可用（HTTP {status_code}）；请稍后重试"
    return f"模型服务拒绝了请求（HTTP {status_code}）；请检查模型名和接口兼容配置"


def _safe_api_base(value: str) -> str:
    """错误信息只显示 API Base 的地址部分，不回显 URL 中潜在的凭据或查询密钥。"""

    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            return "<invalid API Base>"
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{host}{path}"
    except ValueError:
        return "<invalid API Base>"


def _response_message(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise RuntimeError("模型服务返回的响应结构无效")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("模型服务未返回可用回答；请检查模型名、额度和接口兼容配置")
    first = choices[0]
    if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
        raise RuntimeError("模型服务返回的响应结构无效")
    return first["message"]


class ScriptedModel:
    """供测试和离线演示使用的确定性模型。"""

    def __init__(self, turns: list[ModelTurn]):
        self.turns = list(turns)

    async def turn(
        self, messages: Sequence[AgentMessage], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        del messages, tools
        if not self.turns:
            raise RuntimeError("ScriptedModel 没有剩余响应")
        return self.turns.pop(0)
