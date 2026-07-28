from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.workflow_streaming import StreamOutputBuffer  # noqa: E402


async def benchmark_stream_buffer() -> dict[str, Any]:
    persisted: list[str] = []

    async def persist(batch: str) -> None:
        persisted.append(batch)

    buffer = StreamOutputBuffer(
        persist,
        flush_interval_seconds=60,
        flush_bytes=8 * 1024,
    )
    delta = "abcdefghij"
    started = time.perf_counter()
    tracemalloc.start()
    for _ in range(10_000):
        await buffer.append(delta)
    stats = await buffer.close()
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    elapsed = time.perf_counter() - started
    output = "".join(persisted)
    if output != delta * 10_000:
        raise RuntimeError("stream benchmark output was lost, duplicated, or reordered")
    return {
        "name": "stream_batching_100k_bytes",
        "input_chunks": stats.received_chunks,
        "input_bytes": stats.received_bytes,
        "persisted_batches": stats.persisted_batches,
        "write_reduction_ratio": round(stats.received_chunks / stats.persisted_batches, 2),
        "elapsed_seconds": round(elapsed, 6),
        "peak_traced_bytes": peak_bytes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(benchmark_stream_buffer())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
