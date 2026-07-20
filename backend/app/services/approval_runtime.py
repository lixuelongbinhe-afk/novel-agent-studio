from __future__ import annotations

import asyncio


class ApprovalSignalBus:
    def __init__(self) -> None:
        self._events: dict[int, asyncio.Event] = {}

    def notify(self, *approval_ids: int) -> None:
        for approval_id in approval_ids:
            self._events.setdefault(approval_id, asyncio.Event()).set()

    async def wait(self, approval_id: int, timeout: float) -> bool:
        event = self._events.setdefault(approval_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.05, timeout))
        except asyncio.TimeoutError:
            return False
        event.clear()
        return True

    def discard(self, approval_id: int) -> None:
        self._events.pop(approval_id, None)


approval_signals = ApprovalSignalBus()
