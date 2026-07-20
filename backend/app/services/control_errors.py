from __future__ import annotations

from app.schemas import NormalizedProviderError


class ModelControlError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int = 409,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.error = NormalizedProviderError(
            code=code,
            message=message,
            retryable=retryable,
            status_code=status_code,
            retry_after_seconds=retry_after_seconds,
        )
        super().__init__(message)
