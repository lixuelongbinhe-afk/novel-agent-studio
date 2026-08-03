from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Generator
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base
from app.schemas import (
    NormalizedContentPart,
    NormalizedMessage,
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedProviderError,
    NormalizedStreamEvent,
    NormalizedUsage,
)


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None, None, None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def add_model(
    db: Session,
    *,
    protocol: str = "mock",
    provider_name: str = "Phase4 Provider",
    model_name: str = "phase4-model",
    context_window: int = 8192,
) -> tuple[models.ProviderAccount, models.ModelProfile]:
    provider = models.ProviderAccount(
        name=provider_name,
        provider_type=protocol,
        enabled=True,
    )
    db.add(provider)
    db.flush()
    db.add(
        models.ProtocolConfiguration(
            provider_account_id=provider.id,
            protocol=protocol,
            options_json="{}",
        )
    )
    profile = models.ModelProfile(
        provider_account_id=provider.id,
        name=model_name,
        display_name=model_name,
        context_window=context_window,
        enabled=True,
    )
    db.add(profile)
    db.flush()
    return provider, profile


def request_for(model: str = "phase4-model", *, text: str = "synthetic input") -> NormalizedModelRequest:
    return NormalizedModelRequest(
        model=model,
        messages=[
            NormalizedMessage(
                role="user", content=[NormalizedContentPart(type="text", text=text)]
            )
        ],
        max_tokens=64,
    )


def response_for(
    request: NormalizedModelRequest,
    *,
    text: str = "ok",
    error: NormalizedProviderError | None = None,
) -> NormalizedModelResponse:
    return NormalizedModelResponse(
        model=request.model,
        text=text if error is None else "",
        content=(
            [NormalizedContentPart(type="text", text=text)] if error is None else []
        ),
        usage=NormalizedUsage(
            input_tokens=5,
            output_tokens=2 if error is None else 0,
            total_tokens=7 if error is None else 5,
            estimated=False,
            source="provider_actual",
        ),
        request_id=f"provider-{id(request)}",
        finish_reason="stop" if error is None else "error",
        error=error,
    )


class ScriptedAdapter:
    def __init__(
        self,
        name: str,
        complete_handler: Callable[
            [NormalizedModelRequest, int], NormalizedModelResponse
        ],
        stream_handler: Callable[
            [NormalizedModelRequest, int], AsyncIterator[NormalizedStreamEvent]
        ]
        | None = None,
    ) -> None:
        self.name = name
        self.complete_handler = complete_handler
        self.stream_handler = stream_handler
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(
        self, request: NormalizedModelRequest, runtime: Any = None
    ) -> NormalizedModelResponse:
        del runtime
        self.complete_calls += 1
        return self.complete_handler(request, self.complete_calls)

    async def stream(
        self, request: NormalizedModelRequest, runtime: Any = None
    ) -> AsyncIterator[NormalizedStreamEvent]:
        del runtime
        self.stream_calls += 1
        if self.stream_handler is None:
            response = self.complete_handler(request, self.stream_calls)
            yield NormalizedStreamEvent(sequence=1, event="start")
            if response.error is not None:
                yield NormalizedStreamEvent(sequence=2, event="error", error=response.error)
            else:
                yield NormalizedStreamEvent(
                    sequence=2, event="delta", text_delta=response.text
                )
                yield NormalizedStreamEvent(
                    sequence=3, event="usage", usage=response.usage
                )
            yield NormalizedStreamEvent(sequence=4, event="done", finish_reason="stop")
            return
        async for event in self.stream_handler(request, self.stream_calls):
            yield event

    async def list_models(self, runtime: Any) -> list[dict[str, Any]]:
        del runtime
        return []


class TransactionInspectingAdapter:
    name = "phase4_transaction_inspecting"

    def __init__(self, db: Session) -> None:
        self.db = db
        self.observations: list[bool] = []

    def observe(self) -> None:
        self.observations.append(self.db.in_transaction())

    async def complete(
        self, request: NormalizedModelRequest, runtime: Any = None
    ) -> NormalizedModelResponse:
        del runtime
        self.observe()
        await asyncio.sleep(0)
        self.observe()
        return response_for(request, text="transaction-free")

    async def stream(
        self, request: NormalizedModelRequest, runtime: Any = None
    ) -> AsyncIterator[NormalizedStreamEvent]:
        del runtime
        self.observe()
        await asyncio.sleep(0)
        self.observe()
        yield NormalizedStreamEvent(sequence=1, event="start")
        yield NormalizedStreamEvent(
            sequence=2, event="delta", text_delta="transaction-free"
        )
        yield NormalizedStreamEvent(
            sequence=3,
            event="usage",
            usage=NormalizedUsage(
                input_tokens=2,
                output_tokens=2,
                total_tokens=4,
                estimated=False,
                source="provider_actual",
            ),
        )
        yield NormalizedStreamEvent(sequence=4, event="done", finish_reason="stop")

    async def list_models(self, runtime: Any) -> list[dict[str, Any]]:
        del runtime
        return []
