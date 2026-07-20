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
    NormalizedUsage,
)
from app.services.adapters.base import (
    HTTPAdapter,
    as_list,
    as_mapping,
    error_response,
    malformed_error,
    messages_as_strings,
    parse_structured,
    stream_error_events,
    text_content,
    tool_call_from_mapping,
    usage_from,
)
from app.services.gateway_http import (
    ProviderRequestError,
    ProviderRuntime,
    join_url,
    new_request_id,
)
from app.services.streaming import iter_ndjson


class OllamaAdapter(HTTPAdapter):
    name = "ollama"

    async def complete(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> NormalizedModelResponse:
        provider = self.require_runtime(runtime)
        request_id = new_request_id()
        try:
            data, response_id = await self.http.request_json(
                "POST",
                join_url(provider.base_url, "api/chat"),
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
        completed = False
        yield NormalizedStreamEvent(sequence=sequence, event="start", request_id=request_id)
        sequence += 1
        try:
            async def chunks() -> AsyncIterator[bytes]:
                upstream = self.http.stream_bytes(
                    "POST",
                    join_url(provider.base_url, "api/chat"),
                    headers={**_headers(provider), "Accept": "application/x-ndjson"},
                    json_body=_payload(request, stream=True),
                    request_id=request_id,
                )
                try:
                    async for chunk, _ in upstream:
                        yield chunk
                finally:
                    await upstream.aclose()

            events = iter_ndjson(chunks())
            async for data in events:
                if not isinstance(data, Mapping):
                    continue
                if data.get("error"):
                    raise ProviderRequestError(
                        NormalizedProviderError(
                            code="provider_internal",
                            message=str(data["error"]),
                            retryable=False,
                            request_id=request_id,
                        )
                    )
                message = as_mapping(data.get("message"))
                text = str(message.get("content") or "")
                if text:
                    yield NormalizedStreamEvent(
                        sequence=sequence,
                        event="delta",
                        text_delta=text,
                        request_id=request_id,
                    )
                    sequence += 1
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for raw_tool in tool_calls:
                        if isinstance(raw_tool, Mapping):
                            yield NormalizedStreamEvent(
                                sequence=sequence,
                                event="tool_call_delta",
                                tool_call=tool_call_from_mapping(raw_tool),
                                request_id=request_id,
                            )
                            sequence += 1
                if data.get("done") is True:
                    yield NormalizedStreamEvent(
                        sequence=sequence,
                        event="usage",
                        usage=_usage(data),
                        request_id=request_id,
                    )
                    sequence += 1
                    completed = True
                    yield NormalizedStreamEvent(
                        sequence=sequence,
                        event="done",
                        finish_reason=str(data.get("done_reason") or "stop"),
                        request_id=request_id,
                    )
                    return
            if not completed:
                raise ProviderRequestError(
                    NormalizedProviderError(
                        code="stream_interrupted",
                        message="Ollama stream ended before done=true",
                        retryable=True,
                        request_id=request_id,
                    )
                )
        except (ProviderRequestError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            if isinstance(exc, ProviderRequestError):
                error = exc.error
            elif isinstance(exc, UnicodeDecodeError):
                error = malformed_error("Ollama stream contained invalid UTF-8", request_id)
            else:
                error = malformed_error("Ollama stream returned invalid NDJSON", request_id)
            for item in stream_error_events(sequence, error):
                yield item
        finally:
            await events.aclose()

    async def list_models(self, runtime: ProviderRuntime) -> list[dict[str, Any]]:
        data, _ = await self.http.request_json(
            "GET", join_url(runtime.base_url, "api/tags"), headers=_headers(runtime)
        )
        raw_models = data.get("models", []) if isinstance(data, Mapping) else []
        return [
            {
                "id": str(item.get("model") or item.get("name")),
                "display_name": str(item.get("name") or item.get("model")),
            }
            for item in raw_models
            if isinstance(item, Mapping) and (item.get("model") or item.get("name"))
        ]


def _headers(runtime: ProviderRuntime) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if runtime.api_key:
        headers["Authorization"] = f"Bearer {runtime.api_key}"
    extra = runtime.options.get("headers")
    if isinstance(extra, Mapping):
        headers.update({str(key): str(value) for key, value in extra.items()})
    return headers


def _payload(request: NormalizedModelRequest, *, stream: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": messages_as_strings(request.messages),
        "stream": stream,
        "options": {"temperature": request.temperature, "num_predict": request.max_tokens},
    }
    if request.top_p is not None:
        payload["options"]["top_p"] = request.top_p
    if request.response_format == "json":
        payload["format"] = request.json_schema or "json"
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in request.tools
        ]
    return payload


def _parse_response(
    data: Any, request: NormalizedModelRequest, request_id: str
) -> NormalizedModelResponse:
    if not isinstance(data, Mapping):
        return error_response(request, malformed_error("Ollama response must be an object", request_id))
    if data.get("error"):
        return error_response(
            request,
            NormalizedProviderError(
                code="provider_internal",
                message=str(data["error"]),
                request_id=request_id,
            ),
        )
    message = as_mapping(data.get("message"))
    text = str(message.get("content") or "")
    tool_calls = [
        tool_call_from_mapping(item)
        for item in as_list(message.get("tool_calls"))
        if isinstance(item, Mapping)
    ]
    content = text_content(text)
    content.extend(
        NormalizedContentPart(
            type="tool_call",
            tool_call_id=tool.id,
            name=tool.name,
            arguments=tool.arguments,
        )
        for tool in tool_calls
    )
    return NormalizedModelResponse(
        model=str(data.get("model") or request.model),
        text=text,
        content=content,
        structured_data=parse_structured(text, request.response_format),
        tool_calls=tool_calls,
        finish_reason=str(data.get("done_reason") or "stop"),
        usage=_usage(data),
        request_id=request_id,
    )


def _usage(value: Mapping[str, Any]) -> NormalizedUsage:
    return usage_from(
        value.get("prompt_eval_count"),
        value.get("eval_count"),
    )
