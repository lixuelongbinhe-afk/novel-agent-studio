from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from types import SimpleNamespace
from typing import Literal, cast

import httpx
import pytest

from app.schemas import (
    NormalizedContentPart,
    NormalizedMessage,
    NormalizedModelRequest,
    NormalizedStreamEvent,
    NormalizedToolDefinition,
)
from app.services.adapters.anthropic import AnthropicMessagesAdapter
from app.services.adapters.base import ModelProtocolAdapter
from app.services.adapters.gemini import GeminiAdapter
from app.services.adapters.ollama import OllamaAdapter
from app.services.adapters.openai import OpenAIChatAdapter, OpenAIResponsesAdapter
from app.services.gateway_http import (
    GatewayHTTPClient,
    ProviderRequestError,
    ProviderRuntime,
    redact_headers,
)
from app.services.streaming import iter_ndjson, iter_sse
from tests.fake_provider import app as fake_provider


AdapterFactory = Callable[[GatewayHTTPClient], ModelProtocolAdapter]


ADAPTERS: list[tuple[str, AdapterFactory, str, str]] = [
    ("openai_responses", OpenAIResponsesAdapter, "http://fake/v1", "fake-openai-model"),
    ("openai_chat", OpenAIChatAdapter, "http://fake/v1", "fake-openai-model"),
    ("anthropic", AnthropicMessagesAdapter, "http://fake/anthropic", "fake-claude"),
    ("gemini", GeminiAdapter, "http://fake/v1beta", "fake-gemini"),
    ("ollama", OllamaAdapter, "http://fake", "fake-ollama"),
]


def request(
    *, response_format: Literal["text", "json"] = "text", tools: bool = False
) -> NormalizedModelRequest:
    return NormalizedModelRequest(
        model="fake-model",
        response_format=response_format,
        json_schema={"type": "object", "properties": {"answer": {"type": "string"}}}
        if response_format == "json"
        else None,
        tools=[
            NormalizedToolDefinition(
                name="lookup",
                description="查找地点",
                input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
            )
        ]
        if tools
        else [],
        messages=[
            NormalizedMessage(
                role="system",
                content=[NormalizedContentPart(type="text", text="只返回测试数据")],
            ),
            NormalizedMessage(
                role="user",
                content=[NormalizedContentPart(type="text", text="描述雾港")],
            ),
        ],
    )


def runtime(protocol: str, base_url: str, scenario: str = "success") -> ProviderRuntime:
    return ProviderRuntime(
        protocol=protocol,
        base_url=base_url,
        api_key="super-secret-test-key",
        options={"headers": {"x-fake-scenario": scenario}},
    )


@pytest.fixture
async def gateway() -> AsyncIterator[GatewayHTTPClient]:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_provider))
    yield GatewayHTTPClient(client)
    await client.aclose()


@pytest.mark.parametrize(("protocol", "factory", "base_url", "model_id"), ADAPTERS)
async def test_protocol_success_usage_and_model_list(
    gateway: GatewayHTTPClient,
    protocol: str,
    factory: AdapterFactory,
    base_url: str,
    model_id: str,
) -> None:
    adapter = factory(gateway)
    provider = runtime(protocol, base_url)
    response = await adapter.complete(request(), provider)
    assert response.error is None
    assert response.text == "雾港回应"
    assert response.usage.total_tokens == 9
    assert response.usage.estimated is False
    models = await adapter.list_models(provider)
    assert [item["id"] for item in models] == [model_id]


@pytest.mark.parametrize(("protocol", "factory", "base_url", "_model_id"), ADAPTERS)
async def test_protocol_structured_and_tool_outputs(
    gateway: GatewayHTTPClient,
    protocol: str,
    factory: AdapterFactory,
    base_url: str,
    _model_id: str,
) -> None:
    adapter = factory(gateway)
    structured = await adapter.complete(
        request(response_format="json"), runtime(protocol, base_url, "structured")
    )
    assert structured.structured_data == {"answer": "雾港"}
    tool = await adapter.complete(
        request(tools=True), runtime(protocol, base_url, "tool")
    )
    assert tool.error is None
    assert tool.tool_calls
    assert tool.tool_calls[0].name == "lookup"


@pytest.mark.parametrize(("protocol", "factory", "base_url", "_model_id"), ADAPTERS)
async def test_protocol_streams_normalized_events(
    gateway: GatewayHTTPClient,
    protocol: str,
    factory: AdapterFactory,
    base_url: str,
    _model_id: str,
) -> None:
    events = [event async for event in factory(gateway).stream(request(), runtime(protocol, base_url))]
    assert events[0].event == "start"
    assert "".join(event.text_delta for event in events if event.event == "delta") == "雾港"
    assert any(event.event == "usage" and event.usage and event.usage.total_tokens == 9 for event in events)
    assert events[-1].event == "done"
    assert events[-1].finish_reason not in {None, "error"}


@pytest.mark.parametrize(("protocol", "factory", "base_url", "_model_id"), ADAPTERS)
@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("status_401", "authentication"),
        ("status_403", "permission"),
        ("status_404", "model_not_found"),
        ("status_429", "quota"),
        ("rate_429", "rate_limit"),
        ("status_500", "provider_internal"),
        ("timeout", "timeout"),
    ],
)
async def test_protocol_normalizes_http_errors(
    gateway: GatewayHTTPClient,
    protocol: str,
    factory: AdapterFactory,
    base_url: str,
    _model_id: str,
    scenario: str,
    code: str,
) -> None:
    response = await factory(gateway).complete(request(), runtime(protocol, base_url, scenario))
    assert response.error is not None
    assert response.error.code == code
    assert "super-secret-test-key" not in response.model_dump_json()


@pytest.mark.parametrize(("protocol", "factory", "base_url", "_model_id"), ADAPTERS)
async def test_protocol_rejects_invalid_json_and_truncates_html_error(
    gateway: GatewayHTTPClient,
    protocol: str,
    factory: AdapterFactory,
    base_url: str,
    _model_id: str,
) -> None:
    invalid = await factory(gateway).complete(
        request(), runtime(protocol, base_url, "invalid_json")
    )
    assert invalid.error is not None
    assert invalid.error.code == "malformed_response"
    html = await factory(gateway).complete(
        request(), runtime(protocol, base_url, "html_error")
    )
    assert html.error is not None
    assert html.error.code == "provider_internal"
    assert len(html.error.message) <= 2000


@pytest.mark.parametrize(("protocol", "factory", "base_url", "_model_id"), ADAPTERS)
async def test_protocol_detects_stream_interruption(
    gateway: GatewayHTTPClient,
    protocol: str,
    factory: AdapterFactory,
    base_url: str,
    _model_id: str,
) -> None:
    events = [
        event
        async for event in factory(gateway).stream(
            request(), runtime(protocol, base_url, "stream_interrupt")
        )
    ]
    errors = [event.error for event in events if event.event == "error"]
    assert errors and errors[0] is not None
    assert errors[0].code == "stream_interrupted"
    assert events[-1].event == "done"
    assert events[-1].finish_reason == "error"


async def test_stream_parsers_handle_utf8_boundaries_multiline_sse_and_half_ndjson() -> None:
    sse_body = 'event: delta\ndata: {"text":"雾"}\ndata: {"tail":"港"}\r\n\r\n'.encode()

    async def one_byte_chunks(value: bytes) -> AsyncIterator[bytes]:
        for byte in value:
            yield bytes([byte])

    events = [event async for event in iter_sse(one_byte_chunks(sse_body))]
    assert len(events) == 1
    assert events[0].event == "delta"
    assert events[0].data == '{"text":"雾"}\n{"tail":"港"}'

    ndjson = '{"text":"雾"}\n{"text":"港"}\n'.encode()
    rows = [row async for row in iter_ndjson(one_byte_chunks(ndjson))]
    assert rows == [{"text": "雾"}, {"text": "港"}]


async def test_http_timeout_and_header_redaction() -> None:
    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fake timeout", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    gateway = GatewayHTTPClient(client)
    with pytest.raises(ProviderRequestError) as caught:
        await gateway.request_json("GET", "http://fake/timeout")
    assert caught.value.error.code == "timeout"
    await client.aclose()
    assert redact_headers(
        {"Authorization": "Bearer secret", "x-goog-api-key": "secret", "Accept": "json"}
    ) == {
        "Authorization": "[REDACTED]",
        "x-goog-api-key": "[REDACTED]",
        "Accept": "json",
    }


async def test_http_response_size_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "response is too large"})

    monkeypatch.setattr(
        "app.services.gateway_http.get_settings",
        lambda: SimpleNamespace(gateway_max_response_bytes=8),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = GatewayHTTPClient(client)
    with pytest.raises(ProviderRequestError) as caught:
        await gateway.request_json("GET", "http://fake/large")
    assert caught.value.error.code == "malformed_response"
    assert "size limit" in caught.value.error.message
    await client.aclose()


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False
        self.release = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":null}]}\n\n'
        await self.release.wait()

    async def aclose(self) -> None:
        self.closed = True
        self.release.set()


async def test_cancelling_consumer_closes_upstream_stream() -> None:
    tracking = TrackingStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=tracking)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAIChatAdapter(GatewayHTTPClient(client))
    stream = cast(
        AsyncGenerator[NormalizedStreamEvent, None],
        adapter.stream(request(), runtime("openai_chat", "http://fake/v1")),
    )
    assert (await anext(stream)).event == "start"
    assert (await anext(stream)).event == "delta"
    await stream.aclose()
    await asyncio.sleep(0)
    assert tracking.closed is True
    await client.aclose()
