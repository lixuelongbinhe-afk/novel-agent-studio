from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.core.config import get_settings
from app.schemas import (
    NormalizedContentPart,
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedProviderError,
    NormalizedStreamEvent,
    NormalizedUsage,
)
from app.services.adapters import (
    AnthropicMessagesAdapter,
    GeminiAdapter,
    GenericJsonHttpAdapter,
    OllamaAdapter,
    OpenAIChatAdapter,
    OpenAIResponsesAdapter,
)
from app.services.adapters.base import ModelProtocolAdapter, request_prompt
from app.services.gateway_http import ProviderRuntime


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ModelProtocolAdapter] = {}

    def register(self, adapter: ModelProtocolAdapter, *aliases: str) -> None:
        for name in (adapter.name, *aliases):
            self._adapters[name] = adapter

    def get(self, name: str) -> ModelProtocolAdapter:
        if name not in self._adapters:
            raise KeyError(f"Adapter {name} is not registered")
        return self._adapters[name]

    def names(self) -> list[str]:
        return sorted(self._adapters)


def estimate_usage(prompt: str, output: str) -> NormalizedUsage:
    input_tokens = max(1, len(prompt) // 2)
    output_tokens = max(1, len(output) // 2)
    return NormalizedUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated=True,
        source="local_approximation",
    )


class MockAdapter:
    name = "mock"

    def __init__(self) -> None:
        self.settings = get_settings()

    async def complete(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> NormalizedModelResponse:
        del runtime
        request_id = f"mock-{uuid.uuid4().hex[:12]}"
        prompt = request_prompt(request)
        if request.scenario == "delay":
            await asyncio.sleep(self.settings.mock_delay_ms / 1000)
        if request.scenario == "timeout":
            await asyncio.sleep(0.01)
            return _mock_error(request, request_id, "timeout", "Mock timeout", True, 504)
        if request.scenario == "rate_limit":
            return _mock_error(request, request_id, "rate_limit", "Mock rate limit", True, 429)
        if request.scenario == "error":
            return _mock_error(
                request, request_id, "provider_internal", "Mock provider error", False, 500
            )
        chapter_range = re.search(r"本次只规划第\s*(\d+)\s*至第\s*(\d+)\s*章", prompt)
        if chapter_range:
            start, end = (int(chapter_range.group(1)), int(chapter_range.group(2)))
            lines = ["# 第一卷 演示规划"]
            for number in range(start, end + 1):
                lines.extend(
                    [
                        f"## 第{number}章 演示章节{number}",
                        f"本章推进第 {number} 个核心冲突，并留下后续钩子。",
                        "### 场景一 推进",
                        "人物采取行动，局势发生可验证的变化。",
                        "### 场景二 对抗",
                        "阻力升级，人物必须付出代价才能继续。",
                        "### 场景三 转折",
                        "本章完成局部转折，并建立下一章的悬念。",
                    ]
                )
            text = "\n".join(lines)
        else:
            text = (
                "【Mock】已根据输入生成一段小说工作台响应："
                f"{prompt[:80] or '请提供创作目标'}。下一步建议拆分场景、检查人物动机，并保留伏笔回收点。"
            )
        structured = None
        if request.response_format == "json":
            structured = (
                _sample_json_schema(request.json_schema, request.json_schema)
                if request.json_schema
                else {
                    "summary": text,
                    "next_actions": ["拆分场景", "检查人物动机", "标记伏笔"],
                    "risk": "mock_only",
                }
            )
            text = json.dumps(structured, ensure_ascii=False)
        return NormalizedModelResponse(
            model=request.model,
            text=text,
            content=[NormalizedContentPart(type="text", text=text)],
            structured_data=structured,
            usage=estimate_usage(prompt, text),
            request_id=request_id,
        )

    async def stream(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> AsyncIterator[NormalizedStreamEvent]:
        del runtime
        request_id = f"mock-{uuid.uuid4().hex[:12]}"
        yield NormalizedStreamEvent(
            sequence=1, event="start", request_id=request_id
        )
        response = await self.complete(request)
        if response.error:
            yield NormalizedStreamEvent(
                sequence=2,
                event="error",
                error=response.error,
                request_id=request_id,
            )
            yield NormalizedStreamEvent(
                sequence=3,
                event="done",
                finish_reason="error",
                request_id=request_id,
            )
            return
        sequence = 2
        for chunk in [response.text[i : i + 12] for i in range(0, len(response.text), 12)]:
            await asyncio.sleep(self.settings.mock_delay_ms / 1000)
            yield NormalizedStreamEvent(
                sequence=sequence,
                event="delta",
                text_delta=chunk,
                request_id=request_id,
            )
            sequence += 1
        yield NormalizedStreamEvent(
            sequence=sequence,
            event="usage",
            usage=response.usage,
            request_id=request_id,
        )
        yield NormalizedStreamEvent(
            sequence=sequence + 1,
            event="done",
            finish_reason=response.finish_reason,
            request_id=request_id,
        )

    async def list_models(self, runtime: ProviderRuntime) -> list[dict[str, Any]]:
        del runtime
        return [
            {
                "id": "mock-novel-v1",
                "display_name": "Mock Novel v1",
                "context_window": 8192,
            }
        ]


def _sample_json_schema(
    schema: dict[str, Any] | None,
    root: dict[str, Any] | None,
) -> Any:
    if not schema:
        return None
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/") and root is not None:
        resolved: Any = root
        for part in reference[2:].split("/"):
            if not isinstance(resolved, dict):
                return None
            resolved = resolved.get(part.replace("~1", "/").replace("~0", "~"))
        return _sample_json_schema(
            resolved if isinstance(resolved, dict) else None, root
        )
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    for union_key in ("anyOf", "oneOf"):
        options = schema.get(union_key)
        if isinstance(options, list):
            preferred = next(
                (
                    option
                    for option in options
                    if isinstance(option, dict) and option.get("type") != "null"
                ),
                options[0] if options else None,
            )
            return _sample_json_schema(
                preferred if isinstance(preferred, dict) else None, root
            )
    all_of = schema.get("allOf")
    if isinstance(all_of, list) and all_of:
        return _sample_json_schema(
            all_of[0] if isinstance(all_of[0], dict) else None, root
        )
    if "default" in schema:
        return schema["default"]
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "null")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        if not isinstance(properties, dict):
            return {}
        return {
            key: _sample_json_schema(value, root)
            for key, value in properties.items()
            if key in required and isinstance(value, dict)
        }
    if schema_type == "array":
        minimum = schema.get("minItems", 0)
        count = int(minimum) if isinstance(minimum, int) else 0
        item_schema = schema.get("items")
        return [
            _sample_json_schema(
                item_schema if isinstance(item_schema, dict) else {}, root
            )
            for _ in range(min(count, 3))
        ]
    if schema_type == "integer":
        minimum = schema.get("minimum", 0)
        return int(minimum) if isinstance(minimum, int | float) else 0
    if schema_type == "number":
        minimum = schema.get("minimum", 0)
        return float(minimum) if isinstance(minimum, int | float) else 0.0
    if schema_type == "boolean":
        return False
    if schema_type == "null":
        return None
    minimum_length = schema.get("minLength", 0)
    if isinstance(minimum_length, int) and minimum_length > 4:
        return "mock" + "x" * (minimum_length - 4)
    return "mock"


def _mock_error(
    request: NormalizedModelRequest,
    request_id: str,
    code: str,
    message: str,
    retryable: bool,
    status_code: int,
) -> NormalizedModelResponse:
    return NormalizedModelResponse(
        model=request.model,
        text="",
        usage=NormalizedUsage(),
        request_id=request_id,
        finish_reason="error",
        error=NormalizedProviderError(
            code=code,
            message=message,
            retryable=retryable,
            status_code=status_code,
            request_id=request_id,
        ),
    )


registry = AdapterRegistry()
if not get_settings().production:
    registry.register(MockAdapter())
registry.register(OpenAIResponsesAdapter())
registry.register(OpenAIChatAdapter(), "openai_compatible")
registry.register(AnthropicMessagesAdapter(), "anthropic_compatible")
registry.register(GeminiAdapter())
registry.register(OllamaAdapter())
registry.register(GenericJsonHttpAdapter())
