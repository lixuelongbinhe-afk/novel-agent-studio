from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from app.schemas import (
    NormalizedContentPart,
    NormalizedMessage,
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedProviderError,
    NormalizedStreamEvent,
    NormalizedToolCall,
    NormalizedUsage,
)
from app.services.gateway_http import (
    GatewayHTTPClient,
    ProviderRequestError,
    ProviderRuntime,
    new_request_id,
    shared_http_client,
)
from app.services.ssrf import PinnedTarget, TargetGuard, TargetSecurityError


class ModelProtocolAdapter(Protocol):
    name: str

    async def complete(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> NormalizedModelResponse: ...

    def stream(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> AsyncIterator[NormalizedStreamEvent]: ...

    async def list_models(self, runtime: ProviderRuntime) -> list[dict[str, Any]]: ...


class HTTPAdapter:
    name = "http"

    def __init__(
        self,
        client: GatewayHTTPClient | None = None,
        target_guard: TargetGuard | None = None,
    ) -> None:
        self.http = client or shared_http_client
        self.target_guard = target_guard or TargetGuard()

    @staticmethod
    def require_runtime(runtime: ProviderRuntime | None) -> ProviderRuntime:
        if runtime is None:
            raise ValueError("Provider runtime is required")
        return runtime

    async def validate_target(
        self, url: str, runtime: ProviderRuntime
    ) -> PinnedTarget:
        security_mode = str(runtime.options.get("security_mode") or "public_only")
        approved_value = runtime.options.get("approved_origin")
        approved_origin = str(approved_value) if approved_value else None
        if (
            "security_mode" not in runtime.options
            and runtime.protocol == "ollama"
            and runtime.base_url.rstrip("/") == "http://127.0.0.1:11434"
        ):
            # Selecting the bundled Ollama preset is approval for this one exact Origin.
            security_mode = "local_private"
            approved_origin = "http://127.0.0.1:11434"
        try:
            return await self.target_guard.validate(
                url,
                security_mode=security_mode,
                approved_origin=approved_origin,
            )
        except TargetSecurityError as exc:
            raise ProviderRequestError(
                NormalizedProviderError(
                    code="invalid_request",
                    message=str(exc),
                    retryable=False,
                    request_id=new_request_id(),
                )
            ) from exc

    @staticmethod
    def target_headers(
        headers: Mapping[str, str], target: PinnedTarget
    ) -> dict[str, str]:
        return {**headers, "Host": target.host_header}


def content_text(content: list[NormalizedContentPart]) -> str:
    parts: list[str] = []
    for part in content:
        if part.text is not None:
            parts.append(part.text)
        elif part.data is not None:
            parts.append(json.dumps(part.data, ensure_ascii=False))
    return "\n".join(parts)


def messages_as_strings(messages: list[NormalizedMessage]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role, "content": content_text(message.content)}
        tool_result = next((part for part in message.content if part.type == "tool_result"), None)
        if tool_result is not None and tool_result.tool_call_id:
            item["tool_call_id"] = tool_result.tool_call_id
        result.append(item)
    return result


def request_prompt(request: NormalizedModelRequest) -> str:
    return "\n".join(content_text(message.content) for message in request.messages)


def parse_structured(text: str, response_format: str) -> dict[str, Any] | None:
    if response_format != "json" or not text.strip():
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else {"value": value}


def usage_from(
    input_tokens: Any = 0,
    output_tokens: Any = 0,
    total_tokens: Any = None,
    *,
    cached_input_tokens: Any = 0,
    reasoning_tokens: Any = 0,
    estimated: bool = False,
) -> NormalizedUsage:
    input_value = _non_negative_int(input_tokens)
    output_value = _non_negative_int(output_tokens)
    total_value = _non_negative_int(total_tokens)
    if total_tokens is None:
        total_value = input_value + output_value
    return NormalizedUsage(
        input_tokens=input_value,
        output_tokens=output_value,
        total_tokens=total_value,
        cached_input_tokens=_non_negative_int(cached_input_tokens),
        reasoning_tokens=_non_negative_int(reasoning_tokens),
        estimated=estimated,
        source="provider_estimate" if estimated else "provider_actual",
    )


def error_response(
    request: NormalizedModelRequest, error: NormalizedProviderError
) -> NormalizedModelResponse:
    return NormalizedModelResponse(
        model=request.model,
        text="",
        usage=NormalizedUsage(),
        request_id=error.request_id or "",
        finish_reason="error",
        error=error,
    )


def malformed_error(message: str, request_id: str = "") -> NormalizedProviderError:
    return NormalizedProviderError(
        code="malformed_response", message=message, request_id=request_id or None
    )


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def tool_call_from_mapping(value: Mapping[str, Any], *, fallback_id: str = "") -> NormalizedToolCall:
    function_value = value.get("function")
    function: Mapping[str, Any] = (
        function_value if isinstance(function_value, Mapping) else value
    )
    name = str(function.get("name") or value.get("name") or "unknown_tool")
    raw_arguments = function.get("arguments", value.get("arguments", {}))
    arguments: dict[str, Any] | str = (
        raw_arguments if isinstance(raw_arguments, (dict, str)) else {}
    )
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            if isinstance(parsed, dict):
                arguments = parsed
        except json.JSONDecodeError:
            pass
    return NormalizedToolCall(
        id=str(value.get("id") or fallback_id), name=name, arguments=arguments
    )


def text_content(text: str) -> list[NormalizedContentPart]:
    return [NormalizedContentPart(type="text", text=text)] if text else []


def stream_error_events(
    sequence: int, error: NormalizedProviderError
) -> tuple[NormalizedStreamEvent, NormalizedStreamEvent]:
    return (
        NormalizedStreamEvent(
            sequence=sequence,
            event="error",
            error=error,
            request_id=error.request_id,
        ),
        NormalizedStreamEvent(
            sequence=sequence + 1,
            event="done",
            finish_reason="error",
            request_id=error.request_id,
        ),
    )


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
