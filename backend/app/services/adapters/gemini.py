from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import quote

from app.schemas import (
    NormalizedContentPart,
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedProviderError,
    NormalizedStreamEvent,
    NormalizedToolCall,
    NormalizedUsage,
)
from app.services.adapters.base import (
    HTTPAdapter,
    as_list,
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


class GeminiAdapter(HTTPAdapter):
    name = "gemini"

    async def complete(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> NormalizedModelResponse:
        provider = self.require_runtime(runtime)
        request_id = new_request_id()
        try:
            data, response_id = await self.http.request_json(
                "POST",
                _model_url(provider, request.model, stream=False),
                headers=_headers(provider),
                json_body=_payload(request),
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
        finish_reason: str | None = None
        yield NormalizedStreamEvent(sequence=sequence, event="start", request_id=request_id)
        sequence += 1
        try:
            async def chunks() -> AsyncIterator[bytes]:
                upstream = self.http.stream_bytes(
                    "POST",
                    _model_url(provider, request.model, stream=True),
                    headers={**_headers(provider), "Accept": "text/event-stream"},
                    json_body=_payload(request),
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
                        malformed_error("Gemini stream returned invalid JSON", request_id)
                    ) from exc
                if not isinstance(data, Mapping):
                    continue
                if isinstance(data.get("promptFeedback"), Mapping) and data["promptFeedback"].get("blockReason"):
                    raise ProviderRequestError(
                        NormalizedProviderError(
                            code="content_refusal",
                            message=f"Gemini blocked the prompt: {data['promptFeedback']['blockReason']}",
                            request_id=request_id,
                        )
                    )
                candidates = data.get("candidates")
                if isinstance(candidates, list) and candidates and isinstance(candidates[0], Mapping):
                    candidate = candidates[0]
                    content = as_mapping(candidate.get("content"))
                    parts = as_list(content.get("parts"))
                    for part in parts:
                        if not isinstance(part, Mapping):
                            continue
                        if part.get("text") is not None:
                            text = str(part.get("text") or "")
                            if text:
                                yield NormalizedStreamEvent(
                                    sequence=sequence,
                                    event="delta",
                                    text_delta=text,
                                    request_id=request_id,
                                )
                                sequence += 1
                        elif isinstance(part.get("functionCall"), Mapping):
                            function = part["functionCall"]
                            yield NormalizedStreamEvent(
                                sequence=sequence,
                                event="tool_call_delta",
                                tool_call=NormalizedToolCall(
                                    id="",
                                    name=str(function.get("name") or "tool"),
                                    arguments=function.get("args") if isinstance(function.get("args"), dict) else {},
                                ),
                                request_id=request_id,
                            )
                            sequence += 1
                    if candidate.get("finishReason"):
                        finish_reason = str(candidate["finishReason"])
                usage = data.get("usageMetadata")
                if isinstance(usage, Mapping):
                    yield NormalizedStreamEvent(
                        sequence=sequence,
                        event="usage",
                        usage=_usage(usage),
                        request_id=request_id,
                    )
                    sequence += 1
            if finish_reason is None:
                raise ProviderRequestError(
                    NormalizedProviderError(
                        code="stream_interrupted",
                        message="Gemini stream ended without a finish reason",
                        retryable=True,
                        request_id=request_id,
                    )
                )
            yield NormalizedStreamEvent(
                sequence=sequence,
                event="done",
                finish_reason=finish_reason,
                request_id=request_id,
            )
        except (ProviderRequestError, UnicodeDecodeError) as exc:
            error = (
                exc.error
                if isinstance(exc, ProviderRequestError)
                else malformed_error("Gemini stream contained invalid UTF-8", request_id)
            )
            for item in stream_error_events(sequence, error):
                yield item
        finally:
            await events.aclose()

    async def list_models(self, runtime: ProviderRuntime) -> list[dict[str, Any]]:
        data, _ = await self.http.request_json(
            "GET", join_url(runtime.base_url, "models"), headers=_headers(runtime)
        )
        raw_models = data.get("models", []) if isinstance(data, Mapping) else []
        models: list[dict[str, Any]] = []
        for item in raw_models:
            if not isinstance(item, Mapping) or not item.get("name"):
                continue
            model_id = str(item["name"]).removeprefix("models/")
            methods = item.get("supportedGenerationMethods", [])
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            models.append(
                {
                    "id": model_id,
                    "display_name": str(item.get("displayName") or model_id),
                    "context_window": _as_int(item.get("inputTokenLimit")) or 8192,
                }
            )
        return models


def _headers(runtime: ProviderRuntime) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if runtime.api_key:
        headers["x-goog-api-key"] = runtime.api_key
    extra = runtime.options.get("headers")
    if isinstance(extra, Mapping):
        headers.update({str(key): str(value) for key, value in extra.items()})
    return headers


def _model_url(runtime: ProviderRuntime, model: str, *, stream: bool) -> str:
    model_id = model.removeprefix("models/")
    method = "streamGenerateContent?alt=sse" if stream else "generateContent"
    return join_url(runtime.base_url, f"models/{quote(model_id, safe='-._')}:{method}")


def _payload(request: NormalizedModelRequest) -> dict[str, Any]:
    system_parts = [content_text(message.content) for message in request.messages if message.role == "system"]
    contents: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role == "system":
            continue
        parts: list[dict[str, Any]] = []
        for part in message.content:
            if part.type in {"text", "json"}:
                parts.append({"text": part.text or json.dumps(part.data or {}, ensure_ascii=False)})
            elif part.type == "tool_call":
                parts.append(
                    {
                        "functionCall": {
                            "name": part.name or "tool",
                            "args": part.arguments if isinstance(part.arguments, dict) else {},
                        }
                    }
                )
            elif part.type == "tool_result":
                parts.append(
                    {
                        "functionResponse": {
                            "name": part.name or "tool",
                            "response": part.data or {"result": part.text or ""},
                        }
                    }
                )
        contents.append(
            {"role": "model" if message.role == "assistant" else "user", "parts": parts}
        )
    generation: dict[str, Any] = {
        "temperature": request.temperature,
        "maxOutputTokens": request.max_tokens,
    }
    if request.top_p is not None:
        generation["topP"] = request.top_p
    if request.response_format == "json":
        generation["responseMimeType"] = "application/json"
        if request.json_schema:
            generation["responseJsonSchema"] = request.json_schema
    payload: dict[str, Any] = {"contents": contents, "generationConfig": generation}
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
    if request.tools:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    }
                    for tool in request.tools
                ]
            }
        ]
    return payload


def _parse_response(
    data: Any, request: NormalizedModelRequest, request_id: str
) -> NormalizedModelResponse:
    if not isinstance(data, Mapping):
        return error_response(request, malformed_error("Gemini response must be an object", request_id))
    feedback = data.get("promptFeedback")
    if isinstance(feedback, Mapping) and feedback.get("blockReason"):
        return error_response(
            request,
            NormalizedProviderError(
                code="content_refusal",
                message=f"Gemini blocked the prompt: {feedback['blockReason']}",
                request_id=request_id,
            ),
        )
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], Mapping):
        return error_response(request, malformed_error("Gemini response has no candidates", request_id))
    candidate = candidates[0]
    content = as_mapping(candidate.get("content"))
    parts = as_list(content.get("parts"))
    text_parts: list[str] = []
    tool_calls: list[NormalizedToolCall] = []
    normalized_content: list[NormalizedContentPart] = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        if part.get("text") is not None:
            text = str(part.get("text") or "")
            text_parts.append(text)
            normalized_content.append(NormalizedContentPart(type="text", text=text))
        elif isinstance(part.get("functionCall"), Mapping):
            function = part["functionCall"]
            tool = NormalizedToolCall(
                id="",
                name=str(function.get("name") or "tool"),
                arguments=function.get("args") if isinstance(function.get("args"), dict) else {},
            )
            tool_calls.append(tool)
            normalized_content.append(
                NormalizedContentPart(type="tool_call", name=tool.name, arguments=tool.arguments)
            )
    text = "".join(text_parts)
    usage = as_mapping(data.get("usageMetadata"))
    return NormalizedModelResponse(
        model=str(data.get("modelVersion") or request.model),
        text=text,
        content=normalized_content,
        structured_data=parse_structured(text, request.response_format),
        tool_calls=tool_calls,
        finish_reason=str(candidate.get("finishReason") or "stop"),
        usage=_usage(usage),
        request_id=str(data.get("responseId") or request_id),
    )


def _usage(value: Mapping[str, Any]) -> NormalizedUsage:
    return usage_from(
        value.get("promptTokenCount"),
        value.get("candidatesTokenCount"),
        value.get("totalTokenCount"),
        cached_input_tokens=value.get("cachedContentTokenCount"),
        reasoning_tokens=value.get("thoughtsTokenCount"),
    )


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
