from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
import threading
from typing import TypeVar


T = TypeVar("T")


class StudioWorker:
    """Run mixed async Provider I/O and sync Studio DB work off the API loop."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._guard = threading.Lock()

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        with self._guard:
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self._thread = threading.Thread(
                    target=self._run_loop,
                    name="studio-async-worker",
                    daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("Studio worker failed to start")
        if self._loop is None:
            raise RuntimeError("Studio worker loop is unavailable")
        return self._loop

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        self._loop = None

    async def run(self, factory: Callable[[], Coroutine[object, object, T]]) -> T:
        loop = self._ensure_started()
        future: Future[T] = asyncio.run_coroutine_threadsafe(factory(), loop)
        return await asyncio.wrap_future(future)

    async def shutdown(self) -> None:
        with self._guard:
            loop = self._loop
            thread = self._thread
            self._thread = None
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        await asyncio.to_thread(thread.join, 5)


studio_worker = StudioWorker()
