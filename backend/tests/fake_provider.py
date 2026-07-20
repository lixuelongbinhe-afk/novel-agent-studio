from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse


app = FastAPI()


ERROR_STATUS = {
    "status_401": 401,
    "status_403": 403,
    "status_404": 404,
    "status_429": 429,
    "rate_429": 429,
    "status_500": 500,
    "timeout": 504,
}


def scenario(request: Request) -> str:
    return request.headers.get("x-fake-scenario", "success")


def maybe_error(request: Request) -> Response | None:
    current = scenario(request)
    if current in ERROR_STATUS:
        status_code = ERROR_STATUS[current]
        message = (
            "quota exceeded"
            if current == "status_429"
            else "too many requests"
            if current == "rate_429"
            else f"fake HTTP {status_code}"
        )
        return JSONResponse({"error": {"message": message}}, status_code=status_code)
    if current == "html_error":
        return Response("<html><body>upstream unavailable</body></html>", status_code=500, media_type="text/html")
    if current == "invalid_json":
        return Response('{"broken":', status_code=200, media_type="application/json")
    return None


@app.get("/v1/models")
async def openai_models(request: Request) -> Response:
    error = maybe_error(request)
    if error:
        return error
    return JSONResponse({"data": [{"id": "fake-openai-model"}]}, headers={"x-request-id": "fake-models"})


@app.post("/v1/chat/completions")
async def openai_chat(request: Request) -> Response:
    error = maybe_error(request)
    if error:
        return error
    body = await request.json()
    current = scenario(request)
    if body.get("stream"):
        events: list[dict[str, Any]] = [
            {"id": "chat-stream", "model": body["model"], "choices": [{"delta": {"content": "雾"}, "finish_reason": None}]},
            {"id": "chat-stream", "model": body["model"], "choices": [{"delta": {"content": "港"}, "finish_reason": "stop"}]},
            {"id": "chat-stream", "model": body["model"], "choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}},
        ]
        return _sse(events, done=current != "stream_interrupt")
    text = json.dumps({"answer": "雾港"}, ensure_ascii=False) if current == "structured" else "雾港回应"
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if current == "tool":
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_weather", "type": "function", "function": {"name": "lookup", "arguments": '{"city":"雾港"}'}}],
        }
    return JSONResponse(
        {
            "id": "chat-fake",
            "model": body["model"],
            "choices": [{"message": message, "finish_reason": "tool_calls" if current == "tool" else "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
        },
        headers={"x-request-id": "chat-request"},
    )


@app.post("/v1/responses")
async def openai_responses(request: Request) -> Response:
    error = maybe_error(request)
    if error:
        return error
    body = await request.json()
    current = scenario(request)
    if body.get("stream"):
        events: list[dict[str, Any]] = [
            {"type": "response.output_text.delta", "delta": "雾"},
            {"type": "response.output_text.delta", "delta": "港"},
        ]
        if current != "stream_interrupt":
            events.append(
                {
                    "type": "response.completed",
                    "response": {"id": "resp-stream", "status": "completed", "usage": {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}},
                }
            )
        return _sse(events, done=False)
    text = json.dumps({"answer": "雾港"}, ensure_ascii=False) if current == "structured" else "雾港回应"
    output: list[dict[str, Any]] = [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
    ]
    if current == "tool":
        output = [{"type": "function_call", "call_id": "call_weather", "name": "lookup", "arguments": '{"city":"雾港"}'}]
    return JSONResponse(
        {
            "id": "resp-fake",
            "model": body["model"],
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
        },
        headers={"x-request-id": "responses-request"},
    )


@app.get("/v1/models-anthropic-placeholder")
async def unused() -> dict[str, str]:
    return {"status": "unused"}


@app.post("/anthropic/v1/messages")
async def anthropic_messages_prefixed(request: Request) -> Response:
    return await _anthropic_messages(request)


@app.get("/anthropic/v1/models")
async def anthropic_models(request: Request) -> Response:
    error = maybe_error(request)
    if error:
        return error
    return JSONResponse({"data": [{"id": "fake-claude", "display_name": "Fake Claude"}]})


async def _anthropic_messages(request: Request) -> Response:
    error = maybe_error(request)
    if error:
        return error
    body = await request.json()
    current = scenario(request)
    if body.get("stream"):
        events: list[tuple[str, dict[str, Any]]] = [
            ("message_start", {"type": "message_start", "message": {"id": "msg-stream", "model": body["model"], "usage": {"input_tokens": 7, "output_tokens": 0}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "雾"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "港"}}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}),
        ]
        if current != "stream_interrupt":
            events.append(("message_stop", {"type": "message_stop"}))
        return _named_sse(events)
    text = json.dumps({"answer": "雾港"}, ensure_ascii=False) if current == "structured" else "雾港回应"
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if current == "tool":
        content = [{"type": "tool_use", "id": "toolu_weather", "name": "lookup", "input": {"city": "雾港"}}]
    return JSONResponse(
        {
            "id": "msg-fake",
            "model": body["model"],
            "content": content,
            "stop_reason": "tool_use" if current == "tool" else "end_turn",
            "usage": {"input_tokens": 7, "output_tokens": 2},
        }
    )


@app.get("/v1beta/models")
async def gemini_models(request: Request) -> Response:
    error = maybe_error(request)
    if error:
        return error
    return JSONResponse(
        {"models": [{"name": "models/fake-gemini", "displayName": "Fake Gemini", "inputTokenLimit": 32768, "supportedGenerationMethods": ["generateContent"]}]}
    )


@app.post("/v1beta/models/{model}:generateContent")
async def gemini_generate(model: str, request: Request) -> Response:
    return await _gemini_response(model, request, stream=False)


@app.post("/v1beta/models/{model}:streamGenerateContent")
async def gemini_stream(model: str, request: Request) -> Response:
    return await _gemini_response(model, request, stream=True)


async def _gemini_response(model: str, request: Request, *, stream: bool) -> Response:
    error = maybe_error(request)
    if error:
        return error
    current = scenario(request)
    if stream:
        events: list[dict[str, Any]] = [
            {"candidates": [{"content": {"parts": [{"text": "雾"}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "港"}]}, **({"finishReason": "STOP"} if current != "stream_interrupt" else {})}], "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2, "totalTokenCount": 9}},
        ]
        return _sse(events, done=False)
    text = json.dumps({"answer": "雾港"}, ensure_ascii=False) if current == "structured" else "雾港回应"
    parts: list[dict[str, Any]] = [{"text": text}]
    if current == "tool":
        parts = [{"functionCall": {"name": "lookup", "args": {"city": "雾港"}}}]
    return JSONResponse(
        {
            "responseId": "gemini-fake",
            "modelVersion": model,
            "candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2, "totalTokenCount": 9},
        }
    )


@app.get("/api/tags")
async def ollama_models(request: Request) -> Response:
    error = maybe_error(request)
    if error:
        return error
    return JSONResponse({"models": [{"name": "fake-ollama", "model": "fake-ollama"}]})


@app.post("/api/chat")
async def ollama_chat(request: Request) -> Response:
    error = maybe_error(request)
    if error:
        return error
    body = await request.json()
    current = scenario(request)
    if body.get("stream"):
        rows = [
            {"model": body["model"], "message": {"role": "assistant", "content": "雾"}, "done": False},
            {"model": body["model"], "message": {"role": "assistant", "content": "港"}, "done": current != "stream_interrupt", "done_reason": "stop", "prompt_eval_count": 7, "eval_count": 2},
        ]
        return _ndjson(rows)
    text = json.dumps({"answer": "雾港"}, ensure_ascii=False) if current == "structured" else "雾港回应"
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if current == "tool":
        message = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "lookup", "arguments": {"city": "雾港"}}}]}
    return JSONResponse(
        {"model": body["model"], "message": message, "done": True, "done_reason": "stop", "prompt_eval_count": 7, "eval_count": 2}
    )


def _sse(events: list[dict[str, Any]], *, done: bool) -> StreamingResponse:
    blocks = [f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events]
    if done:
        blocks.append("data: [DONE]\n\n")
    return StreamingResponse(_fragmented("".join(blocks).encode()), media_type="text/event-stream")


def _named_sse(events: list[tuple[str, dict[str, Any]]]) -> StreamingResponse:
    body = "".join(
        f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n" for name, data in events
    )
    return StreamingResponse(_fragmented(body.encode()), media_type="text/event-stream")


def _ndjson(rows: list[dict[str, Any]]) -> StreamingResponse:
    body = "".join(f"{json.dumps(row, ensure_ascii=False)}\n" for row in rows)
    return StreamingResponse(_fragmented(body.encode()), media_type="application/x-ndjson")


async def _fragmented(body: bytes) -> AsyncIterator[bytes]:
    sizes = (1, 2, 5, 3, 8, 13)
    offset = 0
    index = 0
    while offset < len(body):
        size = sizes[index % len(sizes)]
        yield body[offset : offset + size]
        offset += size
        index += 1
        await asyncio.sleep(0)
