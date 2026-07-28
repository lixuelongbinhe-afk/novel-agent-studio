from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class NodeExecutionResult:
    node_key: str
    status: str
    output: Any = None
    error: dict[str, Any] | None = None


class WorkflowNodeError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def value(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}
