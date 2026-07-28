from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import Draft202012Validator, ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas import (
    ModelDebugRequest,
    NormalizedContentPart,
    NormalizedMessage,
)
from app.services import model_execution
from app.services.workflow_events import WorkflowEventBus
from app.services.workflow_streaming import StreamOutputBuffer
from app.services.workflow_types import WorkflowNodeError


@dataclass(frozen=True)
class WorkflowModelExecutionHooks:
    session_factory: Callable[[], Session]
    event_bus: WorkflowEventBus
    snapshot_item: Callable[[int, str, int], dict[str, Any]]
    plan_node: Callable[[int, str], dict[str, Any]]
    check_budget: Callable[[int, str, dict[str, Any], Any], None]
    invocation_totals: Callable[[str], dict[str, Any]]
    update_attempt_accounting: Callable[[int, dict[str, Any]], Awaitable[None]]
    update_node_warnings: Callable[[int, str, list[str]], Awaitable[None]]


async def execute_agent_attempt(
    run_id: int,
    project_id: int,
    workflow_id: int,
    node_key: str,
    attempt_id: int,
    attempt_number: int,
    agent: dict[str, Any],
    system_prompt: str,
    prompt: str,
    *,
    hooks: WorkflowModelExecutionHooks,
) -> Any:
    """Execute one model attempt without owning workflow scheduling state."""
    profile_id = cast(int | None, agent.get("model_profile_id"))
    route_id = cast(int | None, agent.get("route_id"))
    model_name = "route-selected"
    provider_id: int | None = None
    if profile_id is not None:
        profile = hooks.snapshot_item(run_id, "models", profile_id)
        model_name = str(profile["name"])
        provider_id = int(profile["provider_account_id"])
    config = cast(dict[str, Any], hooks.plan_node(run_id, node_key).get("config", {}))
    messages = []
    if system_prompt:
        messages.append(
            NormalizedMessage(
                role="system",
                content=[NormalizedContentPart(type="text", text=system_prompt)],
            )
        )
    messages.append(
        NormalizedMessage(
            role="user", content=[NormalizedContentPart(type="text", text=prompt)]
        )
    )
    parameters = cast(dict[str, Any], agent.get("parameters", {}))
    output_mode = str(agent.get("output_mode", "text"))
    output_schema = cast(dict[str, Any], agent.get("output_schema", {}))
    correlation_id = f"workflow-{run_id}-node-{node_key}-attempt-{attempt_number}"
    payload = ModelDebugRequest(
        provider_account_id=provider_id,
        model_profile_id=profile_id,
        route_id=route_id,
        manual_model_profile_id=cast(int | None, config.get("manual_model_profile_id")),
        project_id=project_id,
        workflow_id=str(workflow_id),
        route_run_id=correlation_id,
        required_capabilities=cast(list[str], agent.get("required_capabilities", [])),
        allow_degradation=bool(agent.get("allow_degradation", True)),
        max_retries=0,
        model=model_name,
        messages=messages,
        stream=True,
        temperature=float(parameters.get("temperature", 0.7)),
        top_p=cast(float | None, parameters.get("top_p")),
        max_tokens=int(parameters.get("max_tokens", 1024)),
        response_format="json" if output_mode == "json" else "text",
        json_schema=output_schema if output_mode == "json" and output_schema else None,
        scenario=cast(Any, parameters.get("scenario", "normal")),
    )
    with hooks.session_factory() as db:
        preflight = model_execution.preflight_execution(db, payload)
        hooks.check_budget(run_id, node_key, agent, preflight)
        db.commit()
        text = ""
        warnings: list[str] = []
        usage: dict[str, Any] = {}
        provider_error: dict[str, Any] | None = None
        settings = get_settings()
        stream_buffer = StreamOutputBuffer(
            lambda delta: hooks.event_bus.emit_stream_checkpoint(
                run_id,
                attempt_id,
                node_key,
                attempt_number,
                delta,
            ),
            flush_interval_seconds=settings.workflow_stream_flush_interval_ms / 1000,
            flush_bytes=settings.workflow_stream_flush_bytes,
        )
        try:
            async for event in model_execution.stream_model(db, payload):
                if event.event == "delta" and event.text_delta:
                    text += event.text_delta
                    await stream_buffer.append(event.text_delta)
                elif event.event == "warning" and event.warning:
                    warnings.append(event.warning)
                    await hooks.event_bus.emit(
                        run_id,
                        "node_warning",
                        node_key=node_key,
                        payload={"attempt": attempt_number, "warning": event.warning},
                    )
                elif event.event == "usage" and event.usage is not None:
                    usage = event.usage.model_dump(mode="json")
                elif event.event == "error" and event.error is not None:
                    provider_error = event.error.model_dump(mode="json")
        finally:
            await stream_buffer.close()
        invocation_values = hooks.invocation_totals(correlation_id)
        await hooks.update_attempt_accounting(attempt_id, invocation_values)
        await hooks.update_node_warnings(run_id, node_key, warnings)
        if provider_error is not None:
            raise WorkflowNodeError(
                str(provider_error.get("code", "provider_error")),
                str(provider_error.get("message", "模型调用失败")),
                retryable=bool(provider_error.get("retryable", False)),
            )
        if output_mode == "json":
            try:
                value = json.loads(text)
                Draft202012Validator(output_schema).validate(value)
            except (json.JSONDecodeError, ValidationError) as exc:
                raise WorkflowNodeError(
                    "output_schema_invalid",
                    str(exc),
                    retryable=True,
                ) from exc
            output: Any = value
        else:
            output = text
        await hooks.event_bus.emit(
            run_id,
            "node_usage",
            node_key=node_key,
            payload={
                "attempt": attempt_number,
                "usage": usage,
                "accounting": invocation_values,
            },
        )
        return output
