from __future__ import annotations

from enum import StrEnum


class ErrorKind(StrEnum):
    """领域错误的语义类别，与传输协议无关。"""

    BAD_REQUEST = "bad_request"
    NOT_FOUND = "not_found"
    INVALID_INPUT = "invalid_input"
    CONFLICT = "conflict"
    PRECONDITION = "precondition"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    BUDGET_PAUSED = "budget_paused"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"
    UPSTREAM_FAILED = "upstream_failed"
    UNAVAILABLE = "unavailable"


class DomainError(Exception):
    """服务层唯一允许抛出的错误基类。"""

    kind: ErrorKind = ErrorKind.INVALID_INPUT

    def __init__(self, detail: object, *, context: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.context = context or {}


class BadRequestError(DomainError):
    kind = ErrorKind.BAD_REQUEST


class NotFoundError(DomainError):
    kind = ErrorKind.NOT_FOUND


class InvalidInputError(DomainError):
    kind = ErrorKind.INVALID_INPUT


class ConflictError(DomainError):
    kind = ErrorKind.CONFLICT


class PreconditionError(DomainError):
    """前置审核未通过、未批准的规划阻止正文生成等。"""

    kind = ErrorKind.PRECONDITION


class PayloadTooLargeError(DomainError):
    kind = ErrorKind.PAYLOAD_TOO_LARGE


class BudgetPausedError(DomainError):
    kind = ErrorKind.BUDGET_PAUSED


class RateLimitedError(DomainError):
    kind = ErrorKind.RATE_LIMITED


class InternalError(DomainError):
    kind = ErrorKind.INTERNAL_ERROR


class UpstreamFailedError(DomainError):
    kind = ErrorKind.UPSTREAM_FAILED


class UnavailableError(DomainError):
    kind = ErrorKind.UNAVAILABLE
