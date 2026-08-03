from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app import models
from app.schemas import (
    CapabilityProbeRequest,
    ModelDebugRequest,
)
from app.services import capabilities, model_execution, model_gateway


from tests.model_control_helpers import (
    ScriptedAdapter,
    TestingSessionLocal,
    TransactionInspectingAdapter,
    add_model,
    clean_database as clean_database,
    request_for,
    response_for,
)


@pytest.mark.asyncio
async def test_provider_waits_never_hold_a_database_transaction() -> None:
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(
            db,
            protocol=TransactionInspectingAdapter.name,
            provider_name="Transaction Boundary Provider",
            model_name="transaction-boundary-model",
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
        adapter = TransactionInspectingAdapter(db)
        model_gateway.registry.register(adapter)
        payload = ModelDebugRequest(
            provider_account_id=provider.id,
            model_profile_id=profile.id,
            model=profile.name,
            messages=request_for(profile.name).messages,
            stream=False,
            max_retries=0,
        )
        response = await model_execution.execute_model(db, payload)
        assert response.error is None
        events = [
            event
            async for event in model_execution.stream_model(
                db, payload.model_copy(update={"stream": True})
            )
        ]

    assert any(event.event == "delta" for event in events)
    assert adapter.observations == [False, False, False, False]


def test_effective_capability_priority_and_current_configuration() -> None:
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(db, protocol="mock")
        db.add_all(
            [
                models.ModelCapability(
                    model_profile_id=profile.id,
                    capability="streaming",
                    status="unsupported",
                    source="automatic_probe",
                ),
                models.ModelCapability(
                    model_profile_id=profile.id,
                    capability="streaming",
                    status="supported",
                    source="official_metadata",
                ),
                models.ModelCapability(
                    model_profile_id=profile.id,
                    capability="streaming",
                    status="degraded",
                    source="manual_override",
                ),
            ]
        )

    with TestingSessionLocal() as db:
        effective = capabilities.effective_capabilities(db, profile.id)
        streaming = next(
            item for item in effective.capabilities if item.capability == "streaming"
        )
        assert (streaming.status, streaming.source) == ("degraded", "manual_override")
        reverted = capabilities.clear_manual_override(db, profile.id, "streaming")
        db.commit()
        streaming = next(
            item for item in reverted.capabilities if item.capability == "streaming"
        )
        assert (streaming.status, streaming.source) == (
            "unsupported",
            "automatic_probe",
        )
        stored_provider = db.get(models.ProviderAccount, provider.id)
        assert stored_provider is not None
        stored_provider.enabled = False
        db.commit()
        disabled = capabilities.effective_capabilities(db, profile.id)
        assert all(item.status == "unsupported" for item in disabled.capabilities)
        assert any("Provider 当前已停用" in item for item in disabled.warnings)


@pytest.mark.asyncio
async def test_capability_probe_is_bounded_and_cancellable() -> None:
    with TestingSessionLocal() as db, db.begin():
        _, profile = add_model(
            db, protocol="mock", model_name="mock-novel-v1"
        )
    with pytest.raises(PydanticValidationError):
        CapabilityProbeRequest(level="advanced", confirm_advanced=False)

    with TestingSessionLocal() as db:
        result = await capabilities.run_capability_probe(
            db, profile.id, CapabilityProbeRequest(level="standard")
        )
        db.commit()
        assert result.status == "completed"
        assert result.request_count == 3
        assert result.results["streaming"] == "supported"
        assert result.results["json_schema"] == "supported"

        advanced = await capabilities.run_capability_probe(
            db,
            profile.id,
            CapabilityProbeRequest(level="advanced", confirm_advanced=True),
        )
        db.commit()
        assert advanced.status == "completed"
        assert advanced.request_count == 4
        assert advanced.results["tool_calling"] == "unknown"

    async def cancelled() -> bool:
        return True

    with TestingSessionLocal() as db:
        with pytest.raises(asyncio.CancelledError):
            await capabilities.run_capability_probe(
                db,
                profile.id,
                CapabilityProbeRequest(level="basic"),
                is_cancelled=cancelled,
            )
        db.commit()
        cancelled_run = db.scalar(
            select(models.CapabilityProbeRun).order_by(
                models.CapabilityProbeRun.id.desc()
            )
        )
        assert cancelled_run is not None
        assert (cancelled_run.status, cancelled_run.error_code) == (
            "cancelled",
            "cancelled",
        )


@pytest.mark.asyncio
async def test_probe_records_missing_credentials_as_failed_without_calling_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.credential_store.get_provider_secret", lambda _provider_id: None
    )
    adapter = ScriptedAdapter(
        "phase4_probe_auth_adapter",
        lambda request, _count: response_for(request),
    )
    model_gateway.registry.register(adapter)
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(
            db,
            protocol=adapter.name,
            provider_name="Probe Auth Provider",
            model_name="probe-auth-model",
        )
        provider.credential_env_var = "PHASE4_MISSING_PROBE_KEY"
        db.add(
            models.ModelPricing(
                model_profile_id=profile.id,
                input_per_million=0,
                output_per_million=0,
                request_fee=0,
                tool_call_fee=0,
                currency="USD",
                effective_from=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        )
    with TestingSessionLocal() as db:
        result = await capabilities.run_capability_probe(
            db, profile.id, CapabilityProbeRequest(level="basic")
        )
        assert result.status == "failed"
        assert result.error_code == "authentication"
        assert result.request_count == 0
        assert adapter.complete_calls == 0
