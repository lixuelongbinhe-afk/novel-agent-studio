from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Generator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.database import Base
from app.schemas import (
    NormalizedContentPart,
    NormalizedMessage,
    NormalizedModelRequest,
    ProviderAccountCreate,
    ProviderAccountUpdate,
)
from app.schemas.custom_api import (
    CredentialReferenceCreate,
    GenericAuthConfig,
    GenericHttpAdapterCreate,
    GenericHttpAdapterSetupCreate,
    GenericHttpAdapterUpdate,
)
from app.services import custom_adapters, model_gateway
from app.services import models as model_service
from app.services.adapters.generic import GenericJsonHttpAdapter
from app.services.gateway_http import GatewayHTTPClient, ProviderRuntime
from app.services.safe_json import (
    SafeMappingError,
    extract_json_path,
    render_safe_template,
    set_json_path,
)
from app.services.ssrf import TargetGuard, TargetSecurityError
from app.services.streaming import iter_chunked_json


fake_api = FastAPI()


@fake_api.post("/generic/json")
async def generic_json(request: Request) -> Response:
    body = await request.json()
    authorization = request.headers.get("authorization", "")
    if body.get("scenario") == "mapped_error":
        return JSONResponse(
            {"fault": {"kind": "custom_failure", "detail": "mapped failure"}},
            status_code=422,
        )
    if body.get("scenario") == "html_error":
        return Response(
            "<html><body>custom upstream unavailable</body></html>",
            status_code=500,
            media_type="text/html",
        )
    return JSONResponse(
        {
            "result": {
                "text": "雾港回应",
                "model": body.get("model"),
                "finish": "stop",
                "request": "generic-request",
            },
            "usage": {"input": 7, "output": 2, "total": 9},
            "received": {
                "temperature": body.get("settings", {}).get("temperature"),
                "authorized": authorization.startswith("Bearer "),
            },
        }
    )


async def _byte_chunks(parts: list[bytes]) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


@fake_api.post("/generic/sse")
async def generic_sse(_request: Request) -> StreamingResponse:
    body = (
        'data: {"delta":"雾","done":false}\r\n\r\n'
        'data: {"delta":"港","done":true,"usage":{"input":3,"output":2,"total":5}}\r\n\r\n'
    ).encode()
    return StreamingResponse(_byte_chunks([body[:37], body[37:61], body[61:]]), media_type="text/event-stream")


@fake_api.post("/generic/ndjson")
async def generic_ndjson(_request: Request) -> StreamingResponse:
    body = '{"delta":"雾","done":false}\n{"delta":"港","done":true}\n'.encode()
    return StreamingResponse(_byte_chunks([body[:21], body[21:39], body[39:]]), media_type="application/x-ndjson")


@fake_api.post("/generic/chunked")
async def generic_chunked(_request: Request) -> StreamingResponse:
    body = '{"delta":"雾","done":false}{"delta":"港","done":true}'.encode()
    return StreamingResponse(_byte_chunks([body[:20], body[20:31], body[31:47], body[47:]]), media_type="application/json")


@fake_api.post("/generic/raw")
async def generic_raw(_request: Request) -> StreamingResponse:
    body = "雾港原文".encode()
    return StreamingResponse(_byte_chunks([body[:1], body[1:4], body[4:]]), media_type="text/plain")


async def public_resolver(_host: str, _port: int) -> list[str]:
    return ["8.8.8.8"]


async def loopback_resolver(_host: str, _port: int) -> list[str]:
    return ["127.0.0.1"]


def request() -> NormalizedModelRequest:
    return NormalizedModelRequest(
        model="custom-model",
        temperature=0.35,
        messages=[
            NormalizedMessage(
                role="system",
                content=[NormalizedContentPart(type="text", text="保持克制")],
            ),
            NormalizedMessage(
                role="user",
                content=[NormalizedContentPart(type="text", text="描写雾港")],
            ),
        ],
    )


def runtime(
    endpoint: str,
    *,
    stream_format: str = "sse",
    stream_mapping: dict[str, object] | None = None,
    auth: dict[str, object] | None = None,
    template: object | None = None,
) -> ProviderRuntime:
    return ProviderRuntime(
        protocol="generic_json_http",
        base_url="http://fake.test",
        api_key="phase3-super-secret",
        options={
            "generic_config": {
                "method": "POST",
                "endpoint": endpoint,
                "content_type": "application/json",
                "response_mode": "json",
                "stream_format": stream_format,
                "security_mode": "public_only",
                "approved_origin": None,
                "query": {},
                "headers": {"X-Client": "NovelAgentStudio"},
                "request_template": template
                or {
                    "model": {"$var": "model"},
                    "messages": {"$var": "messages"},
                    "settings": {},
                },
                "parameter_mapping": {"temperature": "$.settings.temperature"},
                "response_mapping": {
                    "text": "$.result.text",
                    "model": "$.result.model",
                    "finish_reason": "$.result.finish",
                    "request_id": "$.result.request",
                    "usage": {
                        "input_tokens": "$.usage.input",
                        "output_tokens": "$.usage.output",
                        "total_tokens": "$.usage.total",
                    },
                },
                "stream_mapping": stream_mapping
                if stream_mapping is not None
                else {
                    "text_delta": "$.delta",
                    "done": "$.done",
                    "usage": {
                        "input_tokens": "$.usage.input",
                        "output_tokens": "$.usage.output",
                        "total_tokens": "$.usage.total",
                    },
                },
                "error_mapping": {
                    "message": "$.fault.detail",
                    "code": "$.fault.kind",
                },
                "auth": auth or {"type": "bearer"},
                "capability_defaults": {},
                "enabled": True,
            }
        },
    )


@pytest.fixture
async def adapter() -> AsyncIterator[GenericJsonHttpAdapter]:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_api))
    yield GenericJsonHttpAdapter(
        GatewayHTTPClient(client), TargetGuard(resolver=public_resolver)
    )
    await client.aclose()


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def test_safe_template_preserves_json_types_and_rejects_executable_syntax() -> None:
    rendered = render_safe_template(
        {
            "model": {"$var": "model"},
            "messages": {"$var": "messages"},
            "temperature": {"$var": "temperature"},
        },
        {"model": "m", "messages": [{"role": "user"}], "temperature": 0.4},
    )
    assert rendered == {
        "model": "m",
        "messages": [{"role": "user"}],
        "temperature": 0.4,
    }
    with pytest.raises(SafeMappingError):
        render_safe_template({"value": {"$var": "__import__"}}, {})
    with pytest.raises(SafeMappingError):
        render_safe_template({"value": "{{ env.SECRET }}"}, {})
    with pytest.raises(SafeMappingError):
        render_safe_template({"$var": "model", "call": "exec"}, {"model": "m"})


def test_safe_json_path_subset_gets_and_sets_without_scripts() -> None:
    value = {"choices": [{"message": {"content": "雾港"}}]}
    assert extract_json_path(value, "$.choices[0].message.content") == "雾港"
    document: dict[str, Any] = {"request": {}}
    set_json_path(document, "$.request.messages[0].text", "hello")
    assert document == {"request": {"messages": [{"text": "hello"}]}}
    for unsafe in ("$..secret", "$.items[*]", "$.items[?(@.x)]", "$.__class__()"):
        with pytest.raises(SafeMappingError):
            extract_json_path(value, unsafe)


async def test_ssrf_guard_blocks_private_metadata_and_requires_exact_local_origin() -> None:
    public_guard = TargetGuard(resolver=loopback_resolver)
    with pytest.raises(TargetSecurityError):
        await public_guard.validate(
            "http://localhost:11434/api/chat",
            security_mode="public_only",
            approved_origin=None,
        )
    with pytest.raises(TargetSecurityError):
        await TargetGuard().validate_for_approval("http://169.254.169.254/latest/meta-data")

    origin, addresses = await public_guard.validate_for_approval(
        "http://localhost:11434/api/chat"
    )
    assert origin == "http://localhost:11434"
    assert addresses == ("127.0.0.1",)
    target = await public_guard.validate(
        "http://localhost:11434/api/chat",
        security_mode="local_private",
        approved_origin=origin,
    )
    assert target.request_url.startswith("http://127.0.0.1:11434/")
    with pytest.raises(TargetSecurityError):
        await public_guard.validate(
            "http://localhost:11435/api/chat",
            security_mode="local_private",
            approved_origin=origin,
        )


async def test_generic_adapter_maps_typed_request_response_usage_and_redacts_key(
    adapter: GenericJsonHttpAdapter,
) -> None:
    provider = runtime(
        "/generic/json",
        template={
            "model": {"$var": "model"},
            "messages": {"$var": "messages"},
            "credential_echo": {"$var": "credential"},
            "settings": {},
        },
    )
    prepared = await adapter.prepare_request(request(), provider, stream=False)
    assert prepared.json_body is not None
    assert prepared.json_body["settings"]["temperature"] == 0.35
    assert prepared.redacted_preview["headers"]["Authorization"] == "[REDACTED]"
    assert prepared.redacted_preview["body"]["credential_echo"] == "[REDACTED]"
    assert "phase3-super-secret" not in json.dumps(
        prepared.redacted_preview, ensure_ascii=False
    )

    response = await adapter.complete(request(), provider)
    assert response.error is None
    assert response.text == "雾港回应"
    assert response.model == "custom-model"
    assert response.usage.total_tokens == 9
    assert response.request_id == "generic-request"
    assert "phase3-super-secret" not in response.model_dump_json()


@pytest.mark.parametrize(
    ("auth", "header", "query_name"),
    [
        ({"type": "none"}, None, None),
        ({"type": "bearer"}, "Authorization", None),
        ({"type": "api_key_header", "header_name": "X-Custom-Key"}, "X-Custom-Key", None),
        ({"type": "custom_header", "header_name": "X-Auth", "prefix": "Key "}, "X-Auth", None),
        ({"type": "query", "query_name": "access_key"}, None, "access_key"),
        ({"type": "basic", "username": "writer"}, "Authorization", None),
    ],
)
async def test_all_supported_auth_modes_use_only_the_bound_credential(
    adapter: GenericJsonHttpAdapter,
    auth: dict[str, object],
    header: str | None,
    query_name: str | None,
) -> None:
    prepared = await adapter.prepare_request(
        request(), runtime("/generic/json", auth=auth), stream=False
    )
    if header:
        assert "phase3-super-secret" in prepared.headers[header] or auth["type"] == "basic"
        assert prepared.redacted_preview["headers"][header] == "[REDACTED]"
    if query_name:
        assert prepared.query[query_name] == "phase3-super-secret"
        assert prepared.redacted_preview["query"][query_name] == "[REDACTED]"
    assert "phase3-super-secret" not in json.dumps(
        prepared.redacted_preview, ensure_ascii=False
    )


@pytest.mark.parametrize(
    ("endpoint", "stream_format", "stream_mapping", "expected"),
    [
        ("/generic/sse", "sse", None, "雾港"),
        ("/generic/ndjson", "ndjson", None, "雾港"),
        ("/generic/chunked", "chunked_json", None, "雾港"),
        (
            "/generic/raw",
            "raw_text",
            {"text_delta": "$raw"},
            "雾港原文",
        ),
    ],
)
async def test_generic_stream_formats_are_incremental_and_normalized(
    adapter: GenericJsonHttpAdapter,
    endpoint: str,
    stream_format: str,
    stream_mapping: dict[str, object] | None,
    expected: str,
) -> None:
    events = [
        event
        async for event in adapter.stream(
            request(),
            runtime(
                endpoint,
                stream_format=stream_format,
                stream_mapping=stream_mapping,
            ),
        )
    ]
    assert events[0].event == "start"
    assert "".join(event.text_delta for event in events if event.event == "delta") == expected
    assert events[-1].event == "done"
    assert not [event for event in events if event.event == "error"]


async def test_true_incremental_chunked_json_parser_handles_concatenated_values() -> None:
    body = '{"text":"雾"}{"text":"港"}[1,2]'.encode()

    async def one_byte() -> AsyncIterator[bytes]:
        for byte in body:
            yield bytes([byte])

    rows = [row async for row in iter_chunked_json(one_byte())]
    assert rows == [{"text": "雾"}, {"text": "港"}, [1, 2]]


async def test_custom_error_mapping_is_applied(adapter: GenericJsonHttpAdapter) -> None:
    provider = runtime(
        "/generic/json",
        template={"model": {"$var": "model"}, "scenario": "mapped_error"},
    )
    response = await adapter.complete(request(), provider)
    assert response.error is not None
    assert response.error.code == "custom_failure"
    assert response.error.message == "mapped failure"
    assert response.error.status_code == 422


async def test_non_json_http_error_keeps_http_semantics(
    adapter: GenericJsonHttpAdapter,
) -> None:
    provider = runtime(
        "/generic/json",
        template={"model": {"$var": "model"}, "scenario": "html_error"},
    )
    response = await adapter.complete(request(), provider)
    assert response.error is not None
    assert response.error.code == "provider_internal"
    assert response.error.status_code == 500
    assert "custom upstream unavailable" in response.error.message


def test_config_schema_rejects_static_credentials_and_absolute_endpoints() -> None:
    common = {"provider_account_id": 1}
    with pytest.raises(ValidationError):
        GenericHttpAdapterCreate.model_validate(
            {**common, "endpoint": "https://attacker.example/steal"}
        )
    with pytest.raises(ValidationError):
        GenericHttpAdapterCreate.model_validate(
            {**common, "headers": {"Authorization": "Bearer leaked"}}
        )
    with pytest.raises(ValidationError):
        GenericHttpAdapterCreate.model_validate(
            {**common, "query": {"api_key": "leaked"}}
        )
    with pytest.raises(ValidationError):
        GenericHttpAdapterCreate.model_validate(
            {**common, "headers": {"X-Auth-Token": "leaked"}}
        )
    with pytest.raises(ValidationError):
        GenericHttpAdapterCreate.model_validate(
            {**common, "request_template": {"api_key": "sk-leaked"}}
        )
    with pytest.raises(ValidationError):
        GenericHttpAdapterCreate.model_validate(
            {**common, "unknown_security_field": "ignored-secret"}
        )
    allowed = GenericHttpAdapterCreate.model_validate(
        {
            **common,
            "request_template": {"api_key": {"$var": "credential"}},
            "credential_reference_id": 1,
        }
    )
    assert allowed.request_template == {"api_key": {"$var": "credential"}}
    with pytest.raises(ValidationError):
        ProviderAccountCreate.model_validate(
            {
                "name": "unsafe",
                "provider_type": "generic_json_http",
                "base_url": "https://user:password@example.com/v1",
            }
        )
    with pytest.raises(ValidationError):
        ProviderAccountCreate.model_validate(
            {
                "name": "unsafe-query",
                "provider_type": "generic_json_http",
                "base_url": "https://example.com/v1?api_key=leaked",
            }
        )


def test_provider_and_adapter_setup_is_one_service_operation(db: Session) -> None:
    created = custom_adapters.create_config_with_provider(
        db,
        GenericHttpAdapterSetupCreate(
            provider_name="Atomic custom provider",
            base_url="https://custom.example/v1",
            endpoint="/chat",
        ),
    )
    provider = db.get(models.ProviderAccount, created.provider_account_id)
    assert provider is not None
    assert provider.name == "Atomic custom provider"
    assert provider.provider_type == "generic_json_http"
    assert created.enabled is False


async def test_configuration_lifecycle_approval_test_enable_and_manifest_are_safe(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = models.ProviderAccount(
        name="本地自定义 API",
        provider_type="generic_json_http",
        base_url="http://localhost:8123",
        enabled=True,
    )
    db.add(provider)
    db.flush()
    db.add(
        models.ProtocolConfiguration(
            provider_account_id=provider.id,
            protocol="generic_json_http",
            options_json="{}",
        )
    )
    credential = custom_adapters.create_credential(
        db,
        CredentialReferenceCreate(
            name="自定义测试凭据", env_var_name="PHASE3_CUSTOM_API_KEY"
        ),
    )
    created = custom_adapters.create_config(
        db,
        GenericHttpAdapterCreate(
            provider_account_id=provider.id,
            credential_reference_id=credential.id,
            endpoint="/generic/json",
            security_mode="local_private",
            request_template={"model": {"$var": "model"}, "settings": {}},
            parameter_mapping={"temperature": "$.settings.temperature"},
            response_mapping={
                "text": "$.result.text",
                "model": "$.result.model",
                "request_id": "$.result.request",
                "usage": {
                    "input_tokens": "$.usage.input",
                    "output_tokens": "$.usage.output",
                    "total_tokens": "$.usage.total",
                },
            },
            auth=GenericAuthConfig(type="bearer"),
        ),
    )
    assert created.enabled is False
    assert created.approval_current is False
    monkeypatch.setenv("PHASE3_CUSTOM_API_KEY", "phase3-bound-secret")

    approved = await custom_adapters.approve_origin(
        db,
        created.id,
        created.revision,
        guard=TargetGuard(resolver=loopback_resolver),
    )
    assert approved.approved_origin == "http://localhost:8123"
    assert approved.approval_current is True

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_api))
    test_adapter = GenericJsonHttpAdapter(
        GatewayHTTPClient(client), TargetGuard(resolver=loopback_resolver)
    )
    original_adapter = model_gateway.registry.get("generic_json_http")
    model_gateway.registry.register(test_adapter)
    try:
        tested = await custom_adapters.test_config(db, created.id, request())
        assert tested.ok is True
        assert "phase3-bound-secret" not in json.dumps(
            tested.model_dump(mode="json"), ensure_ascii=False
        )
        current = custom_adapters.config_read(
            db, custom_adapters.get_config(db, created.id)
        )
        assert current.test_current is True

        enabled = custom_adapters.update_config(
            db,
            current.id,
            GenericHttpAdapterUpdate.model_validate(
                {
                    **current.model_dump(
                        include=set(GenericHttpAdapterUpdate.model_fields)
                        - {"expected_revision"}
                    ),
                    "enabled": True,
                    "expected_revision": current.revision,
                }
            ),
        )
        assert enabled.enabled is True
        assert enabled.test_current is True
    finally:
        model_gateway.registry.register(original_adapter)
        await client.aclose()

    model_service.update_provider(
        db,
        provider.id,
        ProviderAccountUpdate(
            name=provider.name,
            provider_type="generic_json_http",
            credential_env_var=None,
            base_url="http://localhost:8124",
            enabled=True,
            expected_revision=provider.revision,
        ),
    )
    revoked = custom_adapters.config_read(db, custom_adapters.get_config(db, created.id))
    assert revoked.approval_current is False
    assert revoked.test_current is False
    assert revoked.enabled is False

    manifest = custom_adapters.export_manifest(db, created.id)
    manifest_json = manifest.model_dump_json()
    assert "phase3-bound-secret" not in manifest_json
    assert "PHASE3_CUSTOM_API_KEY" not in manifest_json
    assert manifest.config.credential_reference_id is None
    imported = custom_adapters.import_manifest(db, manifest)
    assert imported.adapter.enabled is False
    assert imported.adapter.credential_reference_id is None
    imported_provider = db.get(models.ProviderAccount, imported.provider_id)
    assert imported_provider is not None and imported_provider.enabled is False

    latest = custom_adapters.config_read(db, custom_adapters.get_config(db, created.id))
    changed = custom_adapters.update_config(
        db,
        latest.id,
        GenericHttpAdapterUpdate.model_validate(
            {
                **latest.model_dump(
                    include=set(GenericHttpAdapterUpdate.model_fields)
                    - {"expected_revision"}
                ),
                "auth": {"type": "custom_header", "header_name": "X-Changed-Key"},
                "enabled": False,
                "expected_revision": latest.revision,
            }
        ),
    )
    assert changed.enabled is False
    assert changed.approval_current is False
    assert changed.test_current is False
    assert changed.approved_origin is None
    assert os.environ["PHASE3_CUSTOM_API_KEY"] == "phase3-bound-secret"
