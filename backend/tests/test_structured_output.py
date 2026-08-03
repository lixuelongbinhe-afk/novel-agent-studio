from __future__ import annotations


import pytest
from sqlalchemy import select

from app import models
from app.schemas import (
    ModelDebugRequest,
    NormalizedContentPart,
    NormalizedMessage,
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedToolDefinition,
)
from app.services import model_execution, model_gateway
from app.services.control_errors import ModelControlError
from app.services.structured_output import extract_json_value, prepare_request


from tests.model_control_helpers import (
    ScriptedAdapter,
    TestingSessionLocal,
    add_model,
    clean_database as clean_database,
    request_for,
    response_for,
)


def test_safe_degradation_and_json_extraction() -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    request = request_for()
    request.response_format = "json"
    request.json_schema = schema
    prepared = prepare_request(
        request,
        {"json_schema": "unsupported", "json_object": "supported"},
        allow_degradation=True,
    )
    assert prepared.structured_mode == "json_object"
    assert prepared.request.json_schema is None
    assert any("本地执行 JSON Schema" in item for item in prepared.warnings)
    assert extract_json_value('prefix {"answer":"ok"} suffix') == {"answer": "ok"}
    assert extract_json_value("not json") is None

    side_effect_request = request_for()
    side_effect_request.tools = [
        NormalizedToolDefinition(
            name="write_database",
            side_effect=True,
            input_schema={"type": "object"},
        )
    ]
    with pytest.raises(ModelControlError, match="副作用"):
        prepare_request(
            side_effect_request,
            {"tool_calling": "unsupported"},
            allow_degradation=True,
        )

    parameter_request = request_for()
    parameter_request.messages.insert(
        0,
        NormalizedMessage(
            role="system",
            content=[NormalizedContentPart(type="text", text="Synthetic system rule")],
        ),
    )
    parameter_request.top_p = 0.8
    parameter_request.temperature = 1.2
    downgraded = prepare_request(
        parameter_request,
        {
            "system_prompt": "emulated",
            "top_p": "unsupported",
            "temperature": "unsupported",
        },
        allow_degradation=True,
    )
    assert all(message.role != "system" for message in downgraded.request.messages)
    assert downgraded.request.top_p is None
    assert downgraded.request.temperature == 0.7
    assert any("System Prompt" in item for item in downgraded.warnings)
    assert any("top_p" in item for item in downgraded.warnings)
    assert any("temperature" in item for item in downgraded.warnings)


@pytest.mark.asyncio
async def test_structured_output_repairs_once_and_records_both_calls() -> None:
    def structured_handler(
        request: NormalizedModelRequest, count: int
    ) -> NormalizedModelResponse:
        return response_for(
            request,
            text="not json" if count == 1 else '{"answer":"repaired"}',
        )

    adapter = ScriptedAdapter("phase4_repair_adapter", structured_handler)
    model_gateway.registry.register(adapter)
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(
            db,
            protocol=adapter.name,
            provider_name="Repair Provider",
            model_name="repair-model",
        )
    with TestingSessionLocal() as db:
        result = await model_execution.execute_model(
            db,
            ModelDebugRequest(
                provider_account_id=provider.id,
                model_profile_id=profile.id,
                model=profile.name,
                messages=request_for(profile.name).messages,
                response_format="json",
                json_schema={
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
                max_tokens=64,
                max_retries=0,
            ),
        )
        assert result.error is None
        assert result.structured_data == {"answer": "repaired"}
        assert adapter.complete_calls == 2
        assert any("一次有限修复" in item for item in result.warnings)
        invocations = db.scalars(
            select(models.ModelInvocation).order_by(models.ModelInvocation.id)
        ).all()
        assert [item.status for item in invocations] == ["failed", "completed"]
        assert invocations[0].error_code == "schema_validation"


@pytest.mark.asyncio
async def test_structured_stream_buffers_validation_and_one_repair_before_output() -> None:
    adapter = ScriptedAdapter(
        "phase4_structured_stream_repair_adapter",
        lambda request, count: response_for(
            request,
            text="invalid" if count == 1 else '{"answer":"stream repaired"}',
        ),
    )
    model_gateway.registry.register(adapter)
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(
            db,
            protocol=adapter.name,
            provider_name="Structured Stream Provider",
            model_name="structured-stream-model",
        )
        db.add(
            models.ModelCapability(
                model_profile_id=profile.id,
                capability="streaming",
                status="supported",
                source="manual_override",
            )
        )
    with TestingSessionLocal() as db:
        events = [
            event
            async for event in model_execution.stream_model(
                db,
                ModelDebugRequest(
                    provider_account_id=provider.id,
                    model_profile_id=profile.id,
                    model=profile.name,
                    messages=request_for(profile.name).messages,
                    response_format="json",
                    json_schema={
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                        "additionalProperties": False,
                    },
                    stream=True,
                    max_retries=0,
                ),
            )
        ]
        text = "".join(event.text_delta for event in events)
        assert text == '{"answer":"stream repaired"}'
        assert "invalid" not in text
        assert any("先缓冲" in (event.warning or "") for event in events)
        assert adapter.complete_calls == 2
        assert adapter.stream_calls == 0
