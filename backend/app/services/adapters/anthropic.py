from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from app.schemas import (
    NormalizedContentPart,
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedProviderError,
    NormalizedStreamEvent,
    NormalizedToolCall,
)
from app.services.adapters.base import (
    HTTPAdapter,
    as_mapping,
    content_text,
    error_response,
    malformed_error,
    parse_structured,
    stream_error_events,
    usage_from,
)
from app.services.gateway_http import (
    ProviderRequestError,
    ProviderRuntime,
    join_url,
    new_request_id,
)
from app.services.streaming import iter_sse


class AnthropicMessagesAdapter(HTTPAdapter):
    name = "anthropic"

    async def complete(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> NormalizedModelResponse:
        provider = self.require_runtime(runtime)
        request_id = new_request_id()
        try:
            data, response_id = await self.http.request_json(
                "POST",
                join_url(provider.base_url, "v1/messages"),
                headers=_headers(provider),
                json_body=_payload(request, stream=False),
                request_id=request_id,
            )
            return _parse_response(data, request, response_id)
        except ProviderRequestError as exc:
            return error_response(request, exc.error)

    async def stream(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> AsyncIterator[NormalizedStreamEvent]:
        provider = self.require_runtime(runtime)
        request_id = new_request_id()
        sequence = 1
        input_tokens = 0
        output_tokens = 0
        stop_reason = "stop"
        tool_blocks: dict[int, tuple[str, str]] = {}
        completed = False
        yield NormalizedStreamEvent(sequence=sequence, event="start", request_id=request_id)
        sequence += 1
        try:
            async def chunks() -> AsyncIterator[bytes]:
                upstream = self.http.stream_bytes(
                    "POST",
                    join_url(provider.base_url, "v1/messages"),
                    headers={**_headers(provider), "Accept": "text/event-stream"},
                    json_body=_payload(request, stream=True),
                    request_id=request_id,
                )
                try:
                    async for chunk, _ in upstream:
                        yield chunk
                finally:
                    await upstream.aclose()

            events = iter_sse(chunks())
            async for event in events:
                try:
                    data = json.loads(event.data)
                except json.JSONDecodeError as exc:
                    raise ProviderRequestError(
                        malformed_error("Anthropic stream returned invalid JSON", request_id)
                    ) from exc
                if not isinstance(data, Mapping):
                    continue
                event_type = str(data.get("type") or event.event)
                if event_type == "message_start":
                    message = as_mapping(data.get("message"))
                    usage = as_mapping(message.get("usage"))
                    input_tokens = _as_int(usage.get("input_tokens"))
                elif event_type == "content_block_start":
                    index = _as_int(data.get("index"))
                    block = as_mapping(data.get("content_block"))
                    if block.get("type") == "tool_use":
                        tool_blocks[index] = (
                            str(block.get("id") or ""),
                            str(block.get("name") or "tool"),
                        )
                elif event_type == "content_block_delta":
                    index = _as_int(data.get("index"))
                    delta = as_mapping(data.get("delta"))
                    if delta.get("type") == "text_delta":
                        text = str(delta.get("text") or "")
                        if text:
                            yield NormalizedStreamEvent(
                                sequence=sequence,
                                event="delta",
                                text_delta=text,
                                request_id=request_id,
                            )
                            sequence += 1
                    elif delta.get("type") == "input_json_delta":
                        tool_id, name = tool_blocks.get(index, ("", "tool"))
                        yield NormalizedStreamEvent(
                            sequence=sequence,
                            event="tool_call_delta",
                            tool_call=NormalizedToolCall(
                                id=tool_id,
                                name=name,
                                arguments=str(delta.get("partial_json") or ""),
                            ),
                            request_id=request_id,
                        )
                        sequence += 1
                elif event_type == "message_delta":
                    delta = as_mapping(data.get("delta"))
                    stop_reason = str(delta.get("stop_reason") or stop_reason)
                    usage = as_mapping(data.get("usage"))
                    output_tokens = _as_int(usage.get("output_tokens"))
                    yield NormalizedStreamEvent(
                        sequence=sequence,
                        event="usage",
                        usage=usage_from(input_tokens, output_tokens),
                        request_id=request_id,
                    )
                    sequence += 1
                elif event_type == "message_stop":
                    completed = True
                    yield NormalizedStreamEvent(
                        sequence=sequence,
                        event="done",
                        finish_reason=stop_reason,
                        request_id=request_id,
                    )
                    return
                elif event_type == "error":
                    raw_error = as_mapping(data.get("error"))
                    error_type = str(raw_error.get("type") or "provider_error")
                    raise ProviderRequestError(
                        NormalizedProviderError(
                            code="provider_internal",
                            message=str(raw_error.get("message") or "Anthropic stream error"),
                            retryable=error_type in {"overloaded_error", "api_error"},
                            request_id=request_id,
                        )
                    )
            if not completed:
                raise ProviderRequestError(
                    NormalizedProviderError(
                        code="stream_interrupted",
                        message="Anthropic stream ended before message_stop",
                        retryable=True,
                        request_id=request_id,
                    )
                )
        except (ProviderRequestError, UnicodeDecodeError) as exc:
            error = (
                exc.error
                if isinstance(exc, ProviderRequestError)
                else malformed_error("Anthropic stream contained invalid UTF-8", request_id)
            )
            for item in stream_error_events(sequence, error):
                yield item
        finally:
            await events.aclose()

    async def list_models(self, runtime: ProviderRuntime) -> list[dict[str, Any]]:
        data, _ = await self.http.request_json(
            "GET", join_url(runtime.base_url, "v1/models"), headers=_headers(runtime)
        )
        raw_models = data.get("data", []) if isinstance(data, Mapping) else []
        return [
            {
                "id": str(item.get("id")),
                "display_name": str(item.get("display_name") or item.get("id")),
            }
            for item in raw_models
            if isinstance(item, Mapping) and item.get("id")
        ]


def _headers(runtime: ProviderRuntime) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "anthropic-version": str(runtime.options.get("anthropic_version") or "2023-06-01"),
    }
    if runtime.api_key:
        headers["x-api-key"] = runtime.api_key
    extra = runtime.options.get("headers")
    if isinstance(extra, Mapping):
        headers.update({str(key): str(value) for key, value in extra.items()})
    return headers


def _payload(request: NormalizedModelRequest, *, stream: bool) -> dict[str, Any]:
    system_parts = [content_text(message.content) for message in request.messages if message.role == "system"]
    messages: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            tool_result_blocks: list[dict[str, Any]] = [
                {
                    "type": "tool_result",
                    "tool_use_id": part.tool_call_id or "",
                    "content": part.text or json.dumps(part.data or {}, ensure_ascii=False),
                }
                for part in message.content
                if part.type == "tool_result"
            ]
            messages.append({"role": "user", "content": tool_result_blocks})
            continue
        content_blocks: list[dict[str, Any]] = []
        for part in message.content:
            if part.type in {"text", "json"}:
                content_blocks.append({"type": "text", "text": part.text or json.dumps(part.data or {}, ensure_ascii=False)})
            elif part.type == "tool_call":
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": part.tool_call_id or "",
                        "name": part.name or "tool",
                        "input": part.arguments if isinstance(part.arguments, dict) else {},
                    }
                )
        messages.append({"role": message.role, "content": content_blocks})
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": messages,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "stream": stream,
    }
    if system_parts:
        payload["system"] = "\n".join(system_parts)
    if request.top_p is not None:
        payload["top_p"] = request.top_p
    if request.tools:
        payload["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in request.tools
        ]
        payload["tool_choice"] = {"type": "any" if request.tool_choice == "required" else request.tool_choice}
    if request.response_format == "json" and request.json_schema:
        payload["output_config"] = {
            "format": {
                "type": "json_schema",
                "schema": request.json_schema,
            }
        }
    return payload


def _parse_response(
    data: Any, request: NormalizedModelRequest, request_id: str
) -> NormalizedModelResponse:
    if not isinstance(data, Mapping):
        return error_response(request, malformed_error("Anthropic response must be an object", request_id))
    content = data.get("content")
    if not isinstance(content, list):
        return error_response(request, malformed_error("Anthropic response has no content", request_id))
    text_parts: list[str] = []
    tool_calls: list[NormalizedToolCall] = []
    normalized_content: list[NormalizedContentPart] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "text":
            text = str(block.get("text") or "")
            text_parts.append(text)
            normalized_content.append(NormalizedContentPart(type="text", text=text))
        elif block.get("type") == "tool_use":
            raw_input = block.get("input")
            tool_call = NormalizedToolCall(
                id=str(block.get("id") or ""),
                name=str(block.get("name") or "tool"),
                arguments=raw_input if isinstance(raw_input, dict) else {},
            )
            tool_calls.append(tool_call)
            normalized_content.append(
                NormalizedContentPart(
                    type="tool_call",
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    arguments=tool_call.arguments,
                )
            )
    text = "".join(text_parts)
    usage = as_mapping(data.get("usage"))
    return NormalizedModelResponse(
        model=str(data.get("model") or request.model),
        text=text,
        content=normalized_content,
        structured_data=parse_structured(text, request.response_format),
        tool_calls=tool_calls,
        finish_reason=str(data.get("stop_reason") or "stop"),
        usage=usage_from(
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            cached_input_tokens=usage.get("cache_read_input_tokens"),
        ),
        request_id=str(data.get("id") or request_id),
    )


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
