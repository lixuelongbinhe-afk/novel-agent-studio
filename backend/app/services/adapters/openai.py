from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing
from typing import Any

from app.schemas import (
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
from app.services.streaming import iter_sse


class OpenAIChatAdapter(HTTPAdapter):
    name = "openai_chat"

    async def complete(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> NormalizedModelResponse:
        provider = self.require_runtime(runtime)
        request_id = new_request_id()
        try:
            data, response_id = await self.http.request_json(
                "POST",
                join_url(provider.base_url, "chat/completions"),
                headers=_openai_headers(provider),
                json_body=_chat_payload(request, stream=False),
                request_id=request_id,
            )
            return _parse_chat_response(data, request, response_id)
        except ProviderRequestError as exc:
            return error_response(request, exc.error)

    async def stream(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> AsyncIterator[NormalizedStreamEvent]:
        provider = self.require_runtime(runtime)
        request_id = new_request_id()
        sequence = 1
        yield NormalizedStreamEvent(
            sequence=sequence, event="start", request_id=request_id
        )
        sequence += 1
        finished = False
        finish_reason: str | None = None
        try:
            async def chunks() -> AsyncIterator[bytes]:
                upstream = self.http.stream_bytes(
                    "POST",
                    join_url(provider.base_url, "chat/completions"),
                    headers={**_openai_headers(provider), "Accept": "text/event-stream"},
                    json_body=_chat_payload(request, stream=True),
                    request_id=request_id,
                )
                try:
                    async for chunk, _ in upstream:
                        yield chunk
                finally:
                    await upstream.aclose()

            async with aclosing(iter_sse(chunks())) as events:
                async for event in events:
                    if event.data.strip() == "[DONE]":
                        finished = True
                        break
                    try:
                        data = json.loads(event.data)
                    except json.JSONDecodeError as exc:
                        raise ProviderRequestError(
                            malformed_error(
                                "OpenAI-compatible stream returned invalid JSON", request_id
                            )
                        ) from exc
                    if not isinstance(data, Mapping):
                        continue
                    if isinstance(data.get("error"), Mapping):
                        error = data["error"]
                        raise ProviderRequestError(
                            NormalizedProviderError(
                                code="provider_internal",
                                message=str(error.get("message") or "Provider stream error"),
                                retryable=False,
                                request_id=request_id,
                            )
                        )
                    choices = data.get("choices")
                    if isinstance(choices, list) and choices:
                        choice = as_mapping(choices[0])
                        delta = as_mapping(choice.get("delta"))
                        text = _chat_content_text(delta.get("content"))
                        if text:
                            yield NormalizedStreamEvent(
                                sequence=sequence,
                                event="delta",
                                text_delta=text,
                                request_id=request_id,
                            )
                            sequence += 1
                        tool_calls = delta.get("tool_calls")
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
                        if choice.get("finish_reason") is not None:
                            finish_reason = str(choice["finish_reason"])
                    if isinstance(data.get("usage"), Mapping):
                        yield NormalizedStreamEvent(
                            sequence=sequence,
                            event="usage",
                            usage=_openai_usage(data["usage"]),
                            request_id=request_id,
                        )
                        sequence += 1
            if not finished:
                raise ProviderRequestError(
                    NormalizedProviderError(
                        code="stream_interrupted",
                        message="OpenAI-compatible stream ended before [DONE]",
                        retryable=True,
                        request_id=request_id,
                    )
                )
            yield NormalizedStreamEvent(
                sequence=sequence,
                event="done",
                finish_reason=finish_reason or "stop",
                request_id=request_id,
            )
        except (ProviderRequestError, UnicodeDecodeError) as exc:
            error = (
                exc.error
                if isinstance(exc, ProviderRequestError)
                else malformed_error("Provider stream contained invalid UTF-8", request_id)
            )
            for item in stream_error_events(sequence, error):
                yield item

    async def list_models(self, runtime: ProviderRuntime) -> list[dict[str, Any]]:
        data, _ = await self.http.request_json(
            "GET", join_url(runtime.base_url, "models"), headers=_openai_headers(runtime)
        )
        raw_models = data.get("data", []) if isinstance(data, Mapping) else []
        return [
            {"id": str(item.get("id")), "display_name": str(item.get("id"))}
            for item in raw_models
            if isinstance(item, Mapping) and item.get("id")
        ]


class OpenAIResponsesAdapter(HTTPAdapter):
    name = "openai_responses"

    async def complete(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> NormalizedModelResponse:
        provider = self.require_runtime(runtime)
        request_id = new_request_id()
        try:
            data, response_id = await self.http.request_json(
                "POST",
                join_url(provider.base_url, "responses"),
                headers=_openai_headers(provider),
                json_body=_responses_payload(request, stream=False),
                request_id=request_id,
            )
            return _parse_responses_response(data, request, response_id)
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
                    join_url(provider.base_url, "responses"),
                    headers={**_openai_headers(provider), "Accept": "text/event-stream"},
                    json_body=_responses_payload(request, stream=True),
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
                        malformed_error("OpenAI Responses stream returned invalid JSON", request_id)
                    ) from exc
                if not isinstance(data, Mapping):
                    continue
                event_type = str(data.get("type") or event.event)
                if event_type == "response.output_text.delta":
                    delta = str(data.get("delta") or "")
                    if delta:
                        yield NormalizedStreamEvent(
                            sequence=sequence,
                            event="delta",
                            text_delta=delta,
                            request_id=request_id,
                        )
                        sequence += 1
                elif event_type == "response.function_call_arguments.delta":
                    yield NormalizedStreamEvent(
                        sequence=sequence,
                        event="tool_call_delta",
                        tool_call=NormalizedToolCall(
                            id=str(data.get("item_id") or ""),
                            name=str(data.get("name") or "function"),
                            arguments=str(data.get("delta") or ""),
                        ),
                        request_id=request_id,
                    )
                    sequence += 1
                elif event_type == "response.refusal.delta":
                    raise ProviderRequestError(
                        NormalizedProviderError(
                            code="content_refusal",
                            message=str(data.get("delta") or "Provider refused the request"),
                            request_id=request_id,
                        )
                    )
                elif event_type == "response.completed":
                    response = as_mapping(data.get("response"))
                    usage = response.get("usage")
                    if isinstance(usage, Mapping):
                        yield NormalizedStreamEvent(
                            sequence=sequence,
                            event="usage",
                            usage=_openai_usage(usage),
                            request_id=request_id,
                        )
                        sequence += 1
                    completed = True
                    yield NormalizedStreamEvent(
                        sequence=sequence,
                        event="done",
                        finish_reason=str(response.get("status") or "stop"),
                        request_id=request_id,
                    )
                    return
                elif event_type in {"response.failed", "error"}:
                    raw_error = data.get("error")
                    message = (
                        str(raw_error.get("message"))
                        if isinstance(raw_error, Mapping)
                        else "OpenAI Responses stream failed"
                    )
                    raise ProviderRequestError(
                        NormalizedProviderError(
                            code="provider_internal",
                            message=message,
                            retryable=True,
                            request_id=request_id,
                        )
                    )
            if not completed:
                raise ProviderRequestError(
                    NormalizedProviderError(
                        code="stream_interrupted",
                        message="OpenAI Responses stream ended before response.completed",
                        retryable=True,
                        request_id=request_id,
                    )
                )
        except (ProviderRequestError, UnicodeDecodeError) as exc:
            error = (
                exc.error
                if isinstance(exc, ProviderRequestError)
                else malformed_error("Provider stream contained invalid UTF-8", request_id)
            )
            for item in stream_error_events(sequence, error):
                yield item
        finally:
            await events.aclose()

    async def list_models(self, runtime: ProviderRuntime) -> list[dict[str, Any]]:
        return await OpenAIChatAdapter(self.http).list_models(runtime)


def _openai_headers(runtime: ProviderRuntime) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if runtime.api_key:
        headers["Authorization"] = f"Bearer {runtime.api_key}"
    extra = runtime.options.get("headers")
    if isinstance(extra, Mapping):
        headers.update({str(key): str(value) for key, value in extra.items()})
    return headers


def _chat_payload(request: NormalizedModelRequest, *, stream: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": messages_as_strings(request.messages),
        "stream": stream,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }
    if request.top_p is not None:
        payload["top_p"] = request.top_p
    if stream:
        payload["stream_options"] = {"include_usage": True}
    if request.response_format == "json":
        payload["response_format"] = (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "novel_agent_output",
                    "strict": True,
                    "schema": request.json_schema,
                },
            }
            if request.json_schema
            else {"type": "json_object"}
        )
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
        payload["tool_choice"] = request.tool_choice
    return payload


def _responses_payload(request: NormalizedModelRequest, *, stream: bool) -> dict[str, Any]:
    system = "\n".join(
        messages_as_strings([message])[0]["content"]
        for message in request.messages
        if message.role == "system"
    )
    input_messages = [
        {
            "role": message.role,
            "content": [
                {"type": "input_text", "text": messages_as_strings([message])[0]["content"]}
            ],
        }
        for message in request.messages
        if message.role != "system"
    ]
    payload: dict[str, Any] = {
        "model": request.model,
        "input": input_messages,
        "stream": stream,
        "temperature": request.temperature,
        "max_output_tokens": request.max_tokens,
        "metadata": request.metadata,
    }
    if system:
        payload["instructions"] = system
    if request.top_p is not None:
        payload["top_p"] = request.top_p
    if request.response_format == "json":
        payload["text"] = {
            "format": (
                {
                    "type": "json_schema",
                    "name": "novel_agent_output",
                    "strict": True,
                    "schema": request.json_schema,
                }
                if request.json_schema
                else {"type": "json_object"}
            )
        }
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in request.tools
        ]
        payload["tool_choice"] = request.tool_choice
    return payload


def _parse_chat_response(
    data: Any, request: NormalizedModelRequest, request_id: str
) -> NormalizedModelResponse:
    if not isinstance(data, Mapping):
        return error_response(request, malformed_error("Chat response must be an object", request_id))
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return error_response(request, malformed_error("Chat response has no choices", request_id))
    choice = choices[0]
    message = as_mapping(choice.get("message"))
    text = _chat_content_text(message.get("content"))
    tool_calls = [
        tool_call_from_mapping(item)
        for item in as_list(message.get("tool_calls"))
        if isinstance(item, Mapping)
    ]
    usage = _openai_usage(as_mapping(data.get("usage")))
    return NormalizedModelResponse(
        model=str(data.get("model") or request.model),
        text=text,
        content=text_content(text),
        structured_data=parse_structured(text, request.response_format),
        tool_calls=tool_calls,
        finish_reason=str(choice.get("finish_reason") or "stop"),
        usage=usage,
        request_id=str(data.get("id") or request_id),
    )


def _parse_responses_response(
    data: Any, request: NormalizedModelRequest, request_id: str
) -> NormalizedModelResponse:
    if not isinstance(data, Mapping):
        return error_response(request, malformed_error("Responses response must be an object", request_id))
    text_parts: list[str] = []
    tool_calls: list[NormalizedToolCall] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            if item.get("type") == "message" and isinstance(item.get("content"), list):
                for part in item["content"]:
                    if isinstance(part, Mapping) and part.get("type") == "output_text":
                        text_parts.append(str(part.get("text") or ""))
                    elif isinstance(part, Mapping) and part.get("type") == "refusal":
                        return error_response(
                            request,
                            NormalizedProviderError(
                                code="content_refusal",
                                message=str(part.get("refusal") or "Provider refused the request"),
                                request_id=request_id,
                            ),
                        )
            elif item.get("type") in {"function_call", "tool_call"}:
                raw_arguments = item.get("arguments")
                tool_calls.append(
                    NormalizedToolCall(
                        id=str(item.get("call_id") or item.get("id") or ""),
                        name=str(item.get("name") or "function"),
                        arguments=raw_arguments if isinstance(raw_arguments, (dict, str)) else {},
                    )
                )
    text = "".join(text_parts)
    usage = _openai_usage(as_mapping(data.get("usage")))
    return NormalizedModelResponse(
        model=str(data.get("model") or request.model),
        text=text,
        content=text_content(text),
        structured_data=parse_structured(text, request.response_format),
        tool_calls=tool_calls,
        finish_reason=str(data.get("status") or "stop"),
        usage=usage,
        request_id=str(data.get("id") or request_id),
    )


def _chat_content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or "")
            for item in value
            if isinstance(item, Mapping) and item.get("type") in {"text", "output_text"}
        )
    return ""


def _openai_usage(value: Mapping[str, Any]) -> NormalizedUsage:
    input_details = value.get("input_tokens_details")
    output_details = value.get("output_tokens_details")
    return usage_from(
        value.get("prompt_tokens", value.get("input_tokens")),
        value.get("completion_tokens", value.get("output_tokens")),
        value.get("total_tokens"),
        cached_input_tokens=(
            input_details.get("cached_tokens", 0) if isinstance(input_details, Mapping) else 0
        ),
        reasoning_tokens=(
            output_details.get("reasoning_tokens", 0) if isinstance(output_details, Mapping) else 0
        ),
    )
