from __future__ import annotations

import codecs
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class SSEEvent:
    event: str
    data: str
    event_id: str | None = None


async def iter_sse(chunks: AsyncIterator[bytes]) -> AsyncGenerator[SSEEvent, None]:
    event_name = "message"
    event_id: str | None = None
    data_lines: list[str] = []
    async with _closing(_iter_lines(chunks)) as lines:
        async for line in lines:
            if line == "":
                if data_lines:
                    yield SSEEvent(
                        event=event_name, data="\n".join(data_lines), event_id=event_id
                    )
                event_name = "message"
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value or "message"
            elif field == "data":
                data_lines.append(value)
            elif field == "id" and "\x00" not in value:
                event_id = value
    if data_lines:
        yield SSEEvent(event=event_name, data="\n".join(data_lines), event_id=event_id)


async def iter_ndjson(chunks: AsyncIterator[bytes]) -> AsyncGenerator[Any, None]:
    async with _closing(_iter_lines(chunks)) as lines:
        async for line in lines:
            if line.strip():
                yield json.loads(line)


async def iter_text(chunks: AsyncIterator[bytes]) -> AsyncGenerator[str, None]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    async with _closing(chunks):
        async for chunk in chunks:
            text = decoder.decode(chunk)
            if text:
                yield text
    tail = decoder.decode(b"", final=True)
    if tail:
        yield tail


async def iter_chunked_json(chunks: AsyncIterator[bytes]) -> AsyncGenerator[Any, None]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    json_decoder = json.JSONDecoder()
    buffer = ""
    async with _closing(chunks):
        async for chunk in chunks:
            buffer += decoder.decode(chunk)
            while buffer.lstrip():
                buffer = buffer.lstrip()
                try:
                    value, end = json_decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    break
                yield value
                buffer = buffer[end:]
    buffer += decoder.decode(b"", final=True)
    while buffer.lstrip():
        buffer = buffer.lstrip()
        value, end = json_decoder.raw_decode(buffer)
        yield value
        buffer = buffer[end:]


async def _iter_lines(chunks: AsyncIterator[bytes]) -> AsyncGenerator[str, None]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    buffer = ""
    async with _closing(chunks):
        async for chunk in chunks:
            buffer += decoder.decode(chunk)
            while True:
                line_break = _next_line_break(buffer, final=False)
                if line_break is None:
                    break
                index, width = line_break
                line, buffer = buffer[:index], buffer[index + width :]
                yield line
    buffer += decoder.decode(b"", final=True)
    while True:
        line_break = _next_line_break(buffer, final=True)
        if line_break is None:
            break
        index, width = line_break
        line, buffer = buffer[:index], buffer[index + width :]
        yield line
    if buffer:
        yield buffer


@asynccontextmanager
async def _closing(iterator: AsyncIterator[T]) -> AsyncIterator[AsyncIterator[T]]:
    try:
        yield iterator
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


def _next_line_break(buffer: str, *, final: bool) -> tuple[int, int] | None:
    for index, character in enumerate(buffer):
        if character == "\n":
            return index, 1
        if character == "\r":
            if index + 1 == len(buffer) and not final:
                return None
            return index, 2 if buffer[index + 1 : index + 2] == "\n" else 1
    return None
