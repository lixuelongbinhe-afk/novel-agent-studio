from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.schemas import NormalizedProviderError


SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-goog-api-key",
    "cookie",
    "set-cookie",
}


@dataclass(frozen=True)
class ProviderRuntime:
    protocol: str
    base_url: str
    api_key: str | None = field(default=None, repr=False)
    options: Mapping[str, Any] = field(default_factory=dict)
    provider_account_id: int | None = None


@dataclass(frozen=True)
class GatewayRawResponse:
    body: bytes
    status_code: int
    headers: Mapping[str, str]
    request_id: str


class ProviderRequestError(Exception):
    def __init__(self, error: NormalizedProviderError) -> None:
        super().__init__(error.message)
        self.error = error


def new_request_id() -> str:
    return f"nas-{uuid.uuid4().hex}"


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        key: "[REDACTED]" if key.lower() in SENSITIVE_HEADERS else value
        for key, value in headers.items()
    }


def join_url(base_url: str, path: str) -> str:
    if not base_url.strip():
        raise ProviderRequestError(
            NormalizedProviderError(
                code="invalid_request", message="Provider Base URL is required", retryable=False
            )
        )
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


class GatewayHTTPClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            settings = get_settings()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=settings.gateway_connect_timeout,
                    read=settings.gateway_read_timeout,
                    write=settings.gateway_write_timeout,
                    pool=settings.gateway_pool_timeout,
                ),
                limits=httpx.Limits(
                    max_connections=settings.gateway_max_connections,
                    max_keepalive_connections=settings.gateway_max_keepalive,
                ),
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any | None = None,
        request_id: str | None = None,
    ) -> tuple[Any, str]:
        response: httpx.Response | None = None
        local_request_id = request_id or new_request_id()
        try:
            response = await self._send(
                method, url, headers=headers, json_body=json_body, request_id=local_request_id
            )
            body = await self._read_limited(response)
            upstream_request_id = _response_request_id(response, local_request_id)
            if response.status_code >= 300:
                raise ProviderRequestError(
                    normalize_http_error(
                        response.status_code,
                        body,
                        request_id=upstream_request_id,
                        content_type=response.headers.get("content-type", ""),
                        retry_after_seconds=_retry_after_seconds(response.headers),
                    )
                )
            try:
                return json.loads(body), upstream_request_id
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ProviderRequestError(
                    NormalizedProviderError(
                        code="malformed_response",
                        message="Provider returned invalid JSON",
                        request_id=upstream_request_id,
                    )
                ) from exc
        except asyncio.CancelledError:
            if response is not None:
                await response.aclose()
            raise
        except httpx.TimeoutException as exc:
            raise ProviderRequestError(
                NormalizedProviderError(
                    code="timeout",
                    message="Provider request timed out",
                    retryable=True,
                    status_code=504,
                    request_id=local_request_id,
                )
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderRequestError(
                NormalizedProviderError(
                    code="connection",
                    message="Unable to connect to provider",
                    retryable=True,
                    request_id=local_request_id,
                )
            ) from exc
        finally:
            if response is not None:
                await response.aclose()

    async def request_raw(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        content: bytes | None = None,
        request_id: str | None = None,
        sni_hostname: str | None = None,
    ) -> GatewayRawResponse:
        response: httpx.Response | None = None
        local_request_id = request_id or new_request_id()
        try:
            response = await self._send(
                method,
                url,
                headers=headers,
                params=params,
                json_body=json_body,
                content=content,
                request_id=local_request_id,
                sni_hostname=sni_hostname,
            )
            body = await self._read_limited(response)
            return GatewayRawResponse(
                body=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                request_id=_response_request_id(response, local_request_id),
            )
        except asyncio.CancelledError:
            if response is not None:
                await response.aclose()
            raise
        except ProviderRequestError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderRequestError(
                NormalizedProviderError(
                    code="timeout",
                    message="Provider request timed out",
                    retryable=True,
                    status_code=504,
                    request_id=local_request_id,
                )
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderRequestError(
                NormalizedProviderError(
                    code="connection",
                    message="Unable to connect to provider",
                    retryable=True,
                    request_id=local_request_id,
                )
            ) from exc
        finally:
            if response is not None:
                await response.aclose()

    async def stream_bytes(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        content: bytes | None = None,
        request_id: str | None = None,
        sni_hostname: str | None = None,
    ) -> AsyncGenerator[tuple[bytes, str], None]:
        response: httpx.Response | None = None
        local_request_id = request_id or new_request_id()
        total = 0
        try:
            response = await self._send(
                method,
                url,
                headers=headers,
                params=params,
                json_body=json_body,
                content=content,
                request_id=local_request_id,
                sni_hostname=sni_hostname,
            )
            upstream_request_id = _response_request_id(response, local_request_id)
            if response.status_code >= 300:
                body = await self._read_limited(response)
                raise ProviderRequestError(
                    normalize_http_error(
                        response.status_code,
                        body,
                        request_id=upstream_request_id,
                        content_type=response.headers.get("content-type", ""),
                        retry_after_seconds=_retry_after_seconds(response.headers),
                    )
                )
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > get_settings().gateway_max_response_bytes:
                    raise ProviderRequestError(
                        NormalizedProviderError(
                            code="malformed_response",
                            message="Provider response exceeded the configured size limit",
                            request_id=upstream_request_id,
                        )
                    )
                if chunk:
                    yield chunk, upstream_request_id
        except asyncio.CancelledError:
            if response is not None:
                await response.aclose()
            raise
        except ProviderRequestError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderRequestError(
                NormalizedProviderError(
                    code="timeout",
                    message="Provider stream timed out",
                    retryable=True,
                    status_code=504,
                    request_id=local_request_id,
                )
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderRequestError(
                NormalizedProviderError(
                    code="stream_interrupted" if response is not None else "connection",
                    message="Provider stream was interrupted",
                    retryable=True,
                    request_id=local_request_id,
                )
            ) from exc
        finally:
            if response is not None:
                await response.aclose()

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None,
        json_body: Any | None,
        request_id: str,
        params: Mapping[str, Any] | None = None,
        content: bytes | None = None,
        sni_hostname: str | None = None,
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("Accept", "application/json")
        request_headers.setdefault("X-Request-ID", request_id)
        request = self._get_client().build_request(
            method,
            url,
            headers=request_headers,
            params=params,
            json=json_body if content is None else None,
            content=content,
        )
        if sni_hostname:
            request.extensions["sni_hostname"] = sni_hostname.encode("ascii")
        return await self._get_client().send(request, stream=True)

    async def _read_limited(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > get_settings().gateway_max_response_bytes:
                raise ProviderRequestError(
                    NormalizedProviderError(
                        code="malformed_response",
                        message="Provider response exceeded the configured size limit",
                        request_id=_response_request_id(response, ""),
                    )
                )
            chunks.append(chunk)
        return b"".join(chunks)


def normalize_http_error(
    status_code: int,
    body: bytes,
    *,
    request_id: str,
    content_type: str = "",
    retry_after_seconds: float | None = None,
) -> NormalizedProviderError:
    message = _safe_error_message(body, content_type)
    lowered = message.lower()
    if status_code == 401:
        code, retryable = "authentication", False
    elif status_code == 403:
        code, retryable = "permission", False
    elif status_code == 404:
        code, retryable = "model_not_found", False
    elif status_code == 429 and ("quota" in lowered or "billing" in lowered):
        code, retryable = "quota", False
    elif status_code == 429:
        code, retryable = "rate_limit", True
    elif status_code in {408, 504}:
        code, retryable = "timeout", True
    elif "context" in lowered and any(word in lowered for word in ("long", "limit", "token")):
        code, retryable = "context_too_long", False
    elif "content" in lowered and any(word in lowered for word in ("refus", "policy", "safety")):
        code, retryable = "content_refusal", False
    elif "unsupported" in lowered or "not support" in lowered:
        code, retryable = "capability_unsupported", False
    elif status_code in {400, 409, 413, 422}:
        code, retryable = "invalid_request", False
    elif status_code >= 500:
        code, retryable = "provider_internal", True
    else:
        code, retryable = "provider_internal", False
    return NormalizedProviderError(
        code=code,
        message=message or f"Provider returned HTTP {status_code}",
        retryable=retryable,
        status_code=status_code,
        request_id=request_id,
        retry_after_seconds=retry_after_seconds,
    )


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _safe_error_message(body: bytes, content_type: str) -> str:
    limit = get_settings().gateway_error_text_limit
    raw = body[: max(limit * 4, limit)].decode("utf-8", errors="replace")
    message = raw
    if "json" in content_type.lower() or raw.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                error = parsed.get("error", parsed)
                if isinstance(error, dict):
                    message = str(error.get("message") or error.get("detail") or error.get("type") or "")
                else:
                    message = str(error)
        except json.JSONDecodeError:
            pass
    message = " ".join(message.replace("\x00", "").split())
    return message[:limit]


def _response_request_id(response: httpx.Response, fallback: str) -> str:
    return (
        response.headers.get("x-request-id")
        or response.headers.get("request-id")
        or response.headers.get("x-goog-request-id")
        or fallback
    )


shared_http_client = GatewayHTTPClient()
