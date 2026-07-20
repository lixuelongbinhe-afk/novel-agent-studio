from __future__ import annotations

import json
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/custom/chat")
async def custom_chat(request: Request):  # type: ignore[no-untyped-def]
    if request.headers.get("authorization") != "Bearer e2e-custom-secret":
        return JSONResponse(
            {"error": {"code": "authentication", "message": "missing credential"}},
            status_code=401,
        )
    body = await request.json()
    if body.get("stream"):
        async def events() -> AsyncIterator[str]:
            records = [
                {
                    "choices": [{"delta": {"content": "雾港"}}],
                    "done": False,
                },
                {
                    "choices": [{"delta": {"content": "自定义流"}}],
                    "done": True,
                    "usage": {
                        "prompt_tokens": 4,
                        "completion_tokens": 4,
                        "total_tokens": 8,
                    },
                },
            ]
            for record in records:
                yield f"data: {json.dumps(record, ensure_ascii=False)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")
    return JSONResponse(
        {
            "id": "e2e-custom-request",
            "model": body.get("model", "custom-model"),
            "choices": [
                {
                    "message": {"content": "雾港自定义 API 回应"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 5,
                "total_tokens": 9,
            },
        }
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8020, log_level="warning")
