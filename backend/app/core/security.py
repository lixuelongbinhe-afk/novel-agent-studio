from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
import secrets

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger(__name__)


def validate_local_api_configuration(*, token: str, production: bool) -> None:
    if production and not token:
        raise RuntimeError(
            "NAS_LOCAL_API_TOKEN is required when NAS_ENV=production"
        )
    if not token:
        logger.warning(
            "Local API authentication is disabled in development because "
            "NAS_LOCAL_API_TOKEN is empty"
        )


class LocalApiTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, token: str, *, require_token: bool = False) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        validate_local_api_configuration(token=token, production=require_token)
        self.token = token

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path.startswith("/api/") and self.token:
            authorization = request.headers.get("authorization", "")
            supplied = (
                authorization.removeprefix("Bearer ")
                if authorization.startswith("Bearer ")
                else request.headers.get("x-nas-token", "")
            )
            if not secrets.compare_digest(supplied, self.token):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "本地 API 会话令牌无效或已过期"},
                )
        return await call_next(request)


class LocalOriginMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, allowed_origins: list[str]) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.allowed_origins = frozenset(origin.rstrip("/") for origin in allowed_origins)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        origin = request.headers.get("origin")
        request_origin = f"{request.url.scheme}://{request.headers.get('host', '')}".rstrip("/")
        if (
            request.url.path.startswith("/api/")
            and origin
            and origin.rstrip("/") not in self.allowed_origins
            and origin.rstrip("/") != request_origin
        ):
            return JSONResponse(
                status_code=403, content={"detail": "请求 Origin 不在本地允许列表中"}
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'; img-src 'self' data: blob:; "
            "font-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'"
        )
        if request.url.path.startswith("/api/") or request.url.path == "/health":
            response.headers["Cache-Control"] = "no-store"
        return response
