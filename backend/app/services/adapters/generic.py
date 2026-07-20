from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from app.schemas import (
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedProviderError,
    NormalizedStreamEvent,
    NormalizedUsage,
)
from app.services.adapters.base import (
    HTTPAdapter,
    as_list,
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
    normalize_http_error,
    redact_headers,
)
from app.services.safe_json import (
    ALLOWED_TEMPLATE_VARIABLES,
    SafeMappingError,
    extract_json_path,
    redact_bound_secret,
    render_safe_template,
    set_json_path,
)
from app.services.ssrf import PinnedTarget, TargetGuard, TargetSecurityError
from app.services.streaming import iter_chunked_json, iter_ndjson, iter_sse, iter_text


@dataclass
class PreparedGenericRequest:
    method: str
    target: PinnedTarget
    headers: dict[str, str]
    query: dict[str, Any]
    json_body: Any | None
    content: bytes | None
    redacted_preview: dict[str, Any]


class GenericJsonHttpAdapter(HTTPAdapter):
    name = "generic_json_http"

    def __init__(self, http=None, target_guard: TargetGuard | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(http)
        self.target_guard = target_guard or TargetGuard()

    async def prepare_request(
        self,
        request: NormalizedModelRequest,
        runtime: ProviderRuntime,
        *,
        stream: bool,
    ) -> PreparedGenericRequest:
        config = _config(runtime)
        endpoint = str(config.get("endpoint") or "/")
        original_url = join_url(runtime.base_url, endpoint)
        try:
            target = await self.target_guard.validate(
                original_url,
                security_mode=str(config.get("security_mode") or "public_only"),
                approved_origin=_optional_string(config.get("approved_origin")),
            )
            variables = _request_variables(request, runtime.api_key, stream)
            body = render_safe_template(config.get("request_template", {}), variables)
            parameter_mapping = _string_mapping(config.get("parameter_mapping"))
            for variable, path in parameter_mapping.items():
                if variable not in ALLOWED_TEMPLATE_VARIABLES:
                    raise SafeMappingError(f"Parameter variable {variable!r} is not allowed")
                body = set_json_path(body, path, variables[variable])
        except (TargetSecurityError, SafeMappingError) as exc:
            raise ProviderRequestError(
                NormalizedProviderError(
                    code="invalid_request",
                    message=str(exc),
                    retryable=False,
                    request_id=new_request_id(),
                )
            ) from exc

        headers = {
            str(key): str(value)
            for key, value in _mapping(config.get("headers")).items()
        }
        headers["Content-Type"] = str(config.get("content_type") or "application/json")
        headers["Host"] = target.host_header
        query = dict(_mapping(config.get("query")))
        auth = _mapping(config.get("auth"))
        _apply_auth(headers, query, auth, runtime.api_key)
        json_body: Any | None = None
        content: bytes | None = None
        if str(config.get("content_type") or "application/json").lower().startswith(
            "application/json"
        ):
            if str(config.get("method") or "POST") != "GET" or body not in ({}, None):
                json_body = body
        elif body is not None:
            content = (
                body.encode("utf-8")
                if isinstance(body, str)
                else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
        preview_query = redact_bound_secret(query, runtime.api_key)
        preview_body = redact_bound_secret(body, runtime.api_key)
        return PreparedGenericRequest(
            method=str(config.get("method") or "POST"),
            target=target,
            headers=headers,
            query=query,
            json_body=json_body,
            content=content,
            redacted_preview={
                "method": str(config.get("method") or "POST"),
                "url": target.original_url,
                "query": preview_query,
                "headers": _redacted_request_headers(headers, auth, runtime.api_key),
                "body": preview_body,
                "resolved_ips": list(target.resolved_ips),
            },
        )

    async def complete(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> NormalizedModelResponse:
        provider = self.require_runtime(runtime)
        request_id = new_request_id()
        try:
            prepared = await self.prepare_request(request, provider, stream=False)
            raw = await self.http.request_raw(
                prepared.method,
                prepared.target.request_url,
                headers=prepared.headers,
                params=prepared.query,
                json_body=prepared.json_body,
                content=prepared.content,
                request_id=request_id,
                sni_hostname=prepared.target.sni_hostname,
            )
            config = _config(provider)
            if raw.status_code >= 300:
                try:
                    data = _decode_body(
                        raw.body, str(config.get("response_mode") or "json")
                    )
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = {}
                raise ProviderRequestError(
                    _mapped_http_error(
                        raw.status_code,
                        raw.body,
                        data,
                        _mapping(config.get("error_mapping")),
                        raw.request_id,
                        raw.headers.get("content-type", ""),
                    )
                )
            data = _decode_body(raw.body, str(config.get("response_mode") or "json"))
            body_error = _mapped_body_error(
                data, _mapping(config.get("error_mapping")), raw.request_id
            )
            if body_error is not None:
                raise ProviderRequestError(body_error)
            return _map_response(
                request,
                data,
                _mapping(config.get("response_mapping")),
                raw.request_id,
                provider.api_key,
            )
        except (ProviderRequestError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            error = (
                exc.error
                if isinstance(exc, ProviderRequestError)
                else malformed_error("Custom API returned a malformed response", request_id)
            )
            return error_response(request, error)

    async def stream(
        self, request: NormalizedModelRequest, runtime: ProviderRuntime | None = None
    ) -> AsyncIterator[NormalizedStreamEvent]:
        provider = self.require_runtime(runtime)
        request_id = new_request_id()
        sequence = 1
        yield NormalizedStreamEvent(sequence=sequence, event="start", request_id=request_id)
        sequence += 1
        try:
            prepared = await self.prepare_request(request, provider, stream=True)
            config = _config(provider)
            stream_mapping = _mapping(config.get("stream_mapping"))

            async def chunks() -> AsyncGenerator[bytes, None]:
                upstream = self.http.stream_bytes(
                    prepared.method,
                    prepared.target.request_url,
                    headers=prepared.headers,
                    params=prepared.query,
                    json_body=prepared.json_body,
                    content=prepared.content,
                    request_id=request_id,
                    sni_hostname=prepared.target.sni_hostname,
                )
                try:
                    async for chunk, _ in upstream:
                        yield chunk
                finally:
                    await upstream.aclose()

            records = _stream_records(str(config.get("stream_format") or "sse"), chunks())
            completed = False
            async with aclosing(records):
                async for record in records:
                    if record == "[DONE]":
                        completed = True
                        break
                    body_error = _mapped_body_error(
                        record, _mapping(config.get("error_mapping")), request_id
                    )
                    if body_error is not None:
                        raise ProviderRequestError(body_error)
                    delta = _mapped(record, stream_mapping.get("text_delta"), "")
                    if delta not in (None, ""):
                        text = str(redact_bound_secret(str(delta), provider.api_key))
                        yield NormalizedStreamEvent(
                            sequence=sequence,
                            event="delta",
                            text_delta=text,
                            request_id=request_id,
                        )
                        sequence += 1
                    raw_tool_calls = _mapped(record, stream_mapping.get("tool_calls"), [])
                    for raw_tool in as_list(raw_tool_calls):
                        if isinstance(raw_tool, Mapping):
                            yield NormalizedStreamEvent(
                                sequence=sequence,
                                event="tool_call_delta",
                                tool_call=tool_call_from_mapping(raw_tool),
                                request_id=request_id,
                            )
                            sequence += 1
                    usage = _mapped_usage(record, stream_mapping.get("usage"))
                    if usage is not None:
                        yield NormalizedStreamEvent(
                            sequence=sequence,
                            event="usage",
                            usage=usage,
                            request_id=request_id,
                        )
                        sequence += 1
                    done_spec = stream_mapping.get("done")
                    if done_spec is not None and bool(_mapped(record, done_spec, False)):
                        completed = True
                        break
            if stream_mapping.get("done") is not None and not completed:
                raise ProviderRequestError(
                    NormalizedProviderError(
                        code="stream_interrupted",
                        message="Custom API stream ended before its configured done signal",
                        retryable=True,
                        request_id=request_id,
                    )
                )
            yield NormalizedStreamEvent(
                sequence=sequence,
                event="done",
                finish_reason="stop",
                request_id=request_id,
            )
        except (
            ProviderRequestError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            SafeMappingError,
        ) as exc:
            if isinstance(exc, ProviderRequestError):
                error = exc.error
            else:
                error = malformed_error("Custom API stream was malformed", request_id)
            for item in stream_error_events(sequence, error):
                yield item

    async def list_models(self, runtime: ProviderRuntime) -> list[dict[str, Any]]:
        del runtime
        return []


def _config(runtime: ProviderRuntime) -> Mapping[str, Any]:
    value = runtime.options.get("generic_config")
    if not isinstance(value, Mapping):
        raise ProviderRequestError(
            NormalizedProviderError(
                code="invalid_request",
                message="Generic HTTP adapter configuration is missing",
                retryable=False,
            )
        )
    return value


def _request_variables(
    request: NormalizedModelRequest, credential: str | None, stream: bool
) -> dict[str, Any]:
    messages = messages_as_strings(request.messages)
    system = "\n".join(
        str(message["content"]) for message in messages if message["role"] == "system"
    )
    prompt = "\n".join(
        str(message["content"]) for message in messages if message["role"] == "user"
    )
    return {
        "model": request.model,
        "messages": messages,
        "system": system,
        "prompt": prompt,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "max_tokens": request.max_tokens,
        "stream": stream,
        "tools": [tool.model_dump() for tool in request.tools],
        "json_schema": request.json_schema,
        "metadata": request.metadata,
        "credential": credential,
    }


def _apply_auth(
    headers: dict[str, str],
    query: dict[str, Any],
    auth: Mapping[str, Any],
    credential: str | None,
) -> None:
    auth_type = str(auth.get("type") or "none")
    if auth_type == "none":
        return
    if not credential:
        raise ProviderRequestError(
            NormalizedProviderError(
                code="authentication",
                message="The bound credential environment variable is not set",
                retryable=False,
            )
        )
    prefix = str(auth.get("prefix") or "")
    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {credential}"
    elif auth_type in {"api_key_header", "custom_header"}:
        name = str(auth.get("header_name") or "")
        if not name:
            raise ProviderRequestError(
                NormalizedProviderError(code="invalid_request", message="Auth header name is missing")
            )
        headers[name] = f"{prefix}{credential}"
    elif auth_type == "query":
        name = str(auth.get("query_name") or "")
        if not name:
            raise ProviderRequestError(
                NormalizedProviderError(code="invalid_request", message="Auth query name is missing")
            )
        query[name] = f"{prefix}{credential}"
    elif auth_type == "basic":
        username = str(auth.get("username") or "")
        token = base64.b64encode(f"{username}:{credential}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    else:
        raise ProviderRequestError(
            NormalizedProviderError(code="invalid_request", message="Unsupported auth type")
        )


def _redacted_request_headers(
    headers: Mapping[str, str],
    auth: Mapping[str, Any],
    credential: str | None,
) -> dict[str, str]:
    redacted = redact_headers(headers)
    if str(auth.get("type") or "none") in {"api_key_header", "custom_header"}:
        configured_name = str(auth.get("header_name") or "")
        for name in list(redacted):
            if name.lower() == configured_name.lower():
                redacted[name] = "[REDACTED]"
    if credential:
        return {name: value.replace(credential, "[REDACTED]") for name, value in redacted.items()}
    return redacted


def _decode_body(body: bytes, response_mode: str) -> Any:
    text = body.decode("utf-8")
    return text if response_mode == "raw_text" else json.loads(text)


def _map_response(
    request: NormalizedModelRequest,
    data: Any,
    mapping: Mapping[str, Any],
    request_id: str,
    credential: str | None,
) -> NormalizedModelResponse:
    raw_text = _mapped(data, mapping.get("text"), data if isinstance(data, str) else "")
    text = str(redact_bound_secret(str(raw_text or ""), credential))
    raw_structured = _mapped(data, mapping.get("structured_data"), None)
    structured = (
        redact_bound_secret(raw_structured, credential)
        if isinstance(raw_structured, dict)
        else parse_structured(text, request.response_format)
    )
    raw_tools = _mapped(data, mapping.get("tool_calls"), [])
    tool_calls = [
        tool_call_from_mapping(item)
        for item in as_list(raw_tools)
        if isinstance(item, Mapping)
    ]
    return NormalizedModelResponse(
        model=str(_mapped(data, mapping.get("model"), request.model) or request.model),
        text=text,
        content=text_content(text),
        structured_data=structured,
        tool_calls=tool_calls,
        finish_reason=str(_mapped(data, mapping.get("finish_reason"), "stop") or "stop"),
        usage=_mapped_usage(data, mapping.get("usage")) or NormalizedUsage(estimated=True),
        request_id=str(_mapped(data, mapping.get("request_id"), request_id) or request_id),
    )


def _mapped(value: Any, spec: Any, default: Any = None) -> Any:
    if spec is None:
        return default
    if isinstance(spec, str) and spec == "$raw":
        return value
    if isinstance(spec, str) and spec.startswith("$"):
        return extract_json_path(value, spec, default)
    return spec


def _mapped_usage(value: Any, spec: Any) -> NormalizedUsage | None:
    if not isinstance(spec, Mapping):
        return None
    input_tokens = _mapped(value, spec.get("input_tokens"), None)
    output_tokens = _mapped(value, spec.get("output_tokens"), None)
    total_tokens = _mapped(value, spec.get("total_tokens"), None)
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return usage_from(input_tokens, output_tokens, total_tokens)


def _mapped_body_error(
    data: Any, mapping: Mapping[str, Any], request_id: str
) -> NormalizedProviderError | None:
    when = mapping.get("when")
    if when is None or not bool(_mapped(data, when, False)):
        return None
    return NormalizedProviderError(
        code=str(_mapped(data, mapping.get("code"), "provider_internal")),
        message=str(_mapped(data, mapping.get("message"), "Custom API returned an error")),
        retryable=bool(_mapped(data, mapping.get("retryable"), False)),
        request_id=request_id,
    )


def _mapped_http_error(
    status_code: int,
    body: bytes,
    data: Any,
    mapping: Mapping[str, Any],
    request_id: str,
    content_type: str,
) -> NormalizedProviderError:
    message = _mapped(data, mapping.get("message"), None)
    code = _mapped(data, mapping.get("code"), None)
    if message is not None or code is not None:
        return NormalizedProviderError(
            code=str(code or "provider_internal"),
            message=str(message or f"Custom API returned HTTP {status_code}"),
            retryable=bool(_mapped(data, mapping.get("retryable"), status_code >= 500)),
            status_code=status_code,
            request_id=request_id,
        )
    return normalize_http_error(
        status_code,
        body,
        request_id=request_id,
        content_type=content_type,
    )


async def _stream_records(
    stream_format: str, chunks: AsyncIterator[bytes]
) -> AsyncGenerator[Any, None]:
    if stream_format == "sse":
        events = iter_sse(chunks)
        async with aclosing(events):
            async for event in events:
                if event.data.strip() == "[DONE]":
                    yield "[DONE]"
                    continue
                try:
                    yield json.loads(event.data)
                except json.JSONDecodeError:
                    yield event.data
        return
    if stream_format == "ndjson":
        records = iter_ndjson(chunks)
    elif stream_format == "chunked_json":
        records = iter_chunked_json(chunks)
    elif stream_format == "raw_text":
        records = iter_text(chunks)
    else:
        raise SafeMappingError("Unsupported stream format")
    async with aclosing(records):
        async for record in records:
            yield record


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_mapping(value: Any) -> dict[str, str]:
    return {
        str(key): str(item)
        for key, item in _mapping(value).items()
        if isinstance(item, str)
    }


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
