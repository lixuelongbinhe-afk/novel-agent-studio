from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import ValidationError
from sqlalchemy.orm import Session

from app import models
from app.schemas import (
    EffectiveCapabilitiesRead,
    ExecutionPreflightRead,
    ModelDebugRequest,
    NormalizedContentPart,
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedProviderError,
    NormalizedStreamEvent,
    NormalizedUsage,
)
from app.services import model_gateway
from app.services import models as model_service
from app.services.capabilities import capability_status_map, effective_capabilities
from app.services.control_errors import ModelControlError
from app.services.gateway_http import ProviderRequestError
from app.services.rate_limits import (
    LimitContext,
    RateLimitLease,
    current_rate_limiter,
    matching_rate_limits,
    retry_delay,
)
from app.services.routing import (
    RouteCandidate,
    RouteResolution,
    claim_provider,
    fallback_allowed,
    record_provider_result,
    resolve_candidates,
)
from app.services.structured_output import (
    PreparedRequest,
    build_repair_request,
    normalize_structured_response,
    prepare_request,
    schema_failure_response,
)
from app.services.usage_control import (
    BudgetContext,
    BudgetReservation,
    active_pricing,
    context_preflight,
    current_budget_manager,
    estimate_cost,
    normalize_usage,
    preflight_cost,
)


CONTROL_FIELDS = {
    "provider_account_id",
    "model_profile_id",
    "route_id",
    "manual_model_profile_id",
    "project_id",
    "workflow_id",
    "route_run_id",
    "required_capabilities",
    "allow_degradation",
    "max_retries",
}


@dataclass
class CallResult:
    response: NormalizedModelResponse
    invocation: models.ModelInvocation
    preflight: ExecutionPreflightRead
    actual_cost: dict[str, Any]


@dataclass
class CandidateResult:
    response: NormalizedModelResponse
    calls: list[CallResult]


def normalized_request(payload: ModelDebugRequest) -> NormalizedModelRequest:
    return NormalizedModelRequest.model_validate(
        payload.model_dump(exclude=CONTROL_FIELDS)
    )


def preflight_execution(
    db: Session, payload: ModelDebugRequest
) -> ExecutionPreflightRead:
    request = normalized_request(payload)
    resolution = _resolution(db, payload, request)
    candidate = resolution.candidates[0]
    return _candidate_preflight(
        db,
        request,
        candidate,
        resolution,
        extra_warnings=list(candidate.capability_warnings),
    )


async def execute_model(
    db: Session, payload: ModelDebugRequest
) -> NormalizedModelResponse:
    request = normalized_request(payload)
    try:
        resolution = _resolution(db, payload, request)
    except ModelControlError as exc:
        return _control_error_response(request, exc.error)

    route_warnings: list[str] = []
    all_calls: list[CallResult] = []
    last_response: NormalizedModelResponse | None = None
    last_candidate = resolution.candidates[0]
    attempt_number = 0
    for candidate_index, candidate in enumerate(resolution.candidates):
        last_candidate = candidate
        retries = payload.max_retries if len(resolution.candidates) == 1 else 0
        for retry_index in range(retries + 1):
            try:
                candidate_result = await _execute_candidate(
                    db,
                    payload,
                    request,
                    resolution,
                    candidate,
                    fallback_count=candidate_index,
                    inherited_warnings=route_warnings,
                )
            except ModelControlError as exc:
                candidate_result = CandidateResult(
                    response=_control_error_response(
                        request.model_copy(update={"model": candidate.profile.name}),
                        exc.error,
                    ),
                    calls=[],
                )
            attempt_number += 1
            all_calls.extend(candidate_result.calls)
            last_response = candidate_result.response
            error = last_response.error
            if error is None:
                return _attach_control(
                    last_response,
                    resolution,
                    candidate,
                    all_calls,
                    candidate_index,
                    route_warnings,
                )
            if retry_index < retries and error.retryable:
                delay = retry_delay(retry_index, error)
                route_warnings.append(
                    f"第 {attempt_number} 次请求失败（{error.code}），"
                    f"将在 {delay:.2f} 秒后重试。"
                )
                await asyncio.sleep(delay)
                continue
            break
        if (
            last_response is not None
            and last_response.error is not None
            and candidate_index + 1 < len(resolution.candidates)
            and fallback_allowed(last_response.error, emitted_text=False)
        ):
            next_candidate = resolution.candidates[candidate_index + 1]
            route_warnings.append(
                f"模型 {candidate.profile.display_name} 返回 {last_response.error.code}；"
                f"已按 Route 规则切换到 {next_candidate.profile.display_name}。"
            )
            continue
        break

    if last_response is None:
        last_response = _control_error_response(
            request,
            NormalizedProviderError(
                code="route_exhausted", message="Route 没有可执行的模型", status_code=503
            ),
        )
    return _attach_control(
        last_response,
        resolution,
        last_candidate,
        all_calls,
        list(resolution.candidates).index(last_candidate),
        route_warnings,
    )


async def stream_model(
    db: Session, payload: ModelDebugRequest
) -> AsyncIterator[NormalizedStreamEvent]:
    request = normalized_request(payload)
    request.stream = True
    try:
        resolution = _resolution(db, payload, request)
    except ModelControlError as exc:
        yield NormalizedStreamEvent(sequence=1, event="error", error=exc.error)
        yield NormalizedStreamEvent(sequence=2, event="done", finish_reason="error")
        return

    # Structured streams are buffered through the normal path so schema repair happens
    # before any unvalidated output is exposed.
    first_capabilities = capability_status_map(
        effective_capabilities(db, resolution.candidates[0].profile.id)
    )
    structured_buffering = (
        request.response_format == "json"
        or request.json_schema is not None
        or bool(request.tools)
    )
    native_streaming = first_capabilities.get("streaming", "unknown") == "supported"
    if structured_buffering or not native_streaming:
        if not native_streaming and not payload.allow_degradation:
            error = NormalizedProviderError(
                code="capability_unsupported",
                message="所选模型不支持原生流式响应",
                status_code=409,
            )
            yield NormalizedStreamEvent(sequence=1, event="error", error=error)
            yield NormalizedStreamEvent(sequence=2, event="done", finish_reason="error")
            return
        normal_payload = payload.model_copy(update={"stream": False})
        response = await execute_model(db, normal_payload)
        sequence = 1
        yield NormalizedStreamEvent(
            sequence=sequence,
            event="start",
            request_id=response.request_id,
        )
        sequence += 1
        yield NormalizedStreamEvent(
            sequence=sequence,
            event="warning",
            warning=(
                "结构化流已先缓冲、校验并执行至多一次有限修复，再输出结果。"
                if structured_buffering
                else "模型不支持原生 streaming；已使用普通响应并明确模拟分块。"
            ),
            request_id=response.request_id,
        )
        sequence += 1
        for warning in response.warnings:
            yield NormalizedStreamEvent(
                sequence=sequence,
                event="warning",
                warning=warning,
                request_id=response.request_id,
            )
            sequence += 1
        if response.error is not None:
            yield NormalizedStreamEvent(
                sequence=sequence,
                event="error",
                error=response.error,
                request_id=response.request_id,
            )
            sequence += 1
        else:
            for offset in range(0, len(response.text), 24):
                yield NormalizedStreamEvent(
                    sequence=sequence,
                    event="delta",
                    text_delta=response.text[offset : offset + 24],
                    request_id=response.request_id,
                )
                sequence += 1
            for tool_call in response.tool_calls:
                yield NormalizedStreamEvent(
                    sequence=sequence,
                    event="tool_call_delta",
                    tool_call=tool_call,
                    request_id=response.request_id,
                )
                sequence += 1
            yield NormalizedStreamEvent(
                sequence=sequence,
                event="usage",
                usage=response.usage,
                request_id=response.request_id,
            )
            sequence += 1
        yield NormalizedStreamEvent(
            sequence=sequence,
            event="done",
            finish_reason=response.finish_reason,
            request_id=response.request_id,
        )
        return

    sequence = 1
    route_warnings: list[str] = []
    for candidate_index, candidate in enumerate(resolution.candidates):
        candidate_request = request.model_copy(update={"model": candidate.profile.name})
        capabilities_read = effective_capabilities(db, candidate.profile.id)
        statuses = capability_status_map(capabilities_read)
        try:
            prepared = prepare_request(
                candidate_request,
                statuses,
                allow_degradation=resolution.allow_degradation,
            )
            preflight = _candidate_preflight(
                db,
                request,
                candidate,
                resolution,
                extra_warnings=[*route_warnings, *candidate.capability_warnings],
                capabilities_read=capabilities_read,
                prepared=prepared,
            )
            candidate_emitted_text = False
            async for item in _stream_candidate(
                db,
                payload,
                resolution,
                candidate,
                prepared,
                preflight,
                fallback_count=candidate_index,
            ):
                event = item.model_copy(update={"sequence": sequence})
                sequence += 1
                if event.event == "delta" and event.text_delta:
                    candidate_emitted_text = True
                if event.event == "error" and event.error is not None:
                    if (
                        candidate_index + 1 < len(resolution.candidates)
                        and fallback_allowed(
                            event.error, emitted_text=candidate_emitted_text
                        )
                    ):
                        next_candidate = resolution.candidates[candidate_index + 1]
                        route_warnings.append(
                            f"流开始前 {candidate.profile.display_name} 返回 {event.error.code}；"
                            f"切换到 {next_candidate.profile.display_name}。"
                        )
                        yield NormalizedStreamEvent(
                            sequence=sequence,
                            event="warning",
                            warning=route_warnings[-1],
                        )
                        sequence += 1
                        break
                yield event
            else:
                return
        except ModelControlError as exc:
            if candidate_index + 1 < len(resolution.candidates) and fallback_allowed(
                exc.error, emitted_text=False
            ):
                route_warnings.append(
                    f"{candidate.profile.display_name} 预检失败（{exc.error.code}），已切换。"
                )
                yield NormalizedStreamEvent(
                    sequence=sequence, event="warning", warning=route_warnings[-1]
                )
                sequence += 1
                continue
            yield NormalizedStreamEvent(sequence=sequence, event="error", error=exc.error)
            sequence += 1
            yield NormalizedStreamEvent(
                sequence=sequence, event="done", finish_reason="error"
            )
            return
    yield NormalizedStreamEvent(
        sequence=sequence,
        event="error",
        error=NormalizedProviderError(
            code="route_exhausted", message="Route 所有模型均失败", status_code=503
        ),
    )
    yield NormalizedStreamEvent(
        sequence=sequence + 1, event="done", finish_reason="error"
    )


def _resolution(
    db: Session, payload: ModelDebugRequest, request: NormalizedModelRequest
) -> RouteResolution:
    return resolve_candidates(
        db,
        request,
        provider_account_id=payload.provider_account_id,
        model_profile_id=payload.model_profile_id,
        route_id=payload.route_id,
        manual_model_profile_id=payload.manual_model_profile_id,
        project_id=payload.project_id,
        route_run_id=payload.route_run_id,
        required_capabilities=payload.required_capabilities,
        allow_degradation=payload.allow_degradation,
    )


def _candidate_preflight(
    db: Session,
    request: NormalizedModelRequest,
    candidate: RouteCandidate,
    resolution: RouteResolution,
    *,
    extra_warnings: list[str],
    capabilities_read: EffectiveCapabilitiesRead | None = None,
    prepared: PreparedRequest | None = None,
) -> ExecutionPreflightRead:
    candidate_request = request.model_copy(update={"model": candidate.profile.name})
    capabilities_value = capabilities_read or effective_capabilities(db, candidate.profile.id)
    prepared_value = prepared or prepare_request(
        candidate_request,
        capability_status_map(capabilities_value),
        allow_degradation=resolution.allow_degradation,
    )
    context = context_preflight(
        prepared_value.request,
        candidate.profile.context_window,
        tokenizer_name=candidate.profile.tokenizer_name,
        tokenizer_source=candidate.profile.tokenizer_source,
    )
    cost = preflight_cost(
        db, candidate.profile.id, prepared_value.request, context
    )
    warnings = [
        *extra_warnings,
        *capabilities_value.warnings,
        *prepared_value.warnings,
        *context.warnings,
    ]
    return ExecutionPreflightRead(
        model_profile_id=candidate.profile.id,
        provider_account_id=candidate.provider.id,
        model_name=candidate.profile.name,
        context=context,
        estimated_cost=cost,
        capabilities=capabilities_value,
        warnings=list(dict.fromkeys(warnings)),
    )


async def _execute_candidate(
    db: Session,
    payload: ModelDebugRequest,
    base_request: NormalizedModelRequest,
    resolution: RouteResolution,
    candidate: RouteCandidate,
    *,
    fallback_count: int,
    inherited_warnings: list[str],
) -> CandidateResult:
    request = base_request.model_copy(update={"model": candidate.profile.name})
    capabilities_read = effective_capabilities(db, candidate.profile.id)
    prepared = prepare_request(
        request,
        capability_status_map(capabilities_read),
        allow_degradation=resolution.allow_degradation,
    )
    preflight = _candidate_preflight(
        db,
        base_request,
        candidate,
        resolution,
        extra_warnings=[*inherited_warnings, *candidate.capability_warnings],
        capabilities_read=capabilities_read,
        prepared=prepared,
    )
    if preflight.context.blocked:
        raise ModelControlError(
            "context_too_long",
            "Context Preflight 预计达到或超过模型上下文窗口",
            status_code=422,
        )
    first = await _call_once(
        db,
        payload,
        resolution,
        candidate,
        prepared.request,
        preflight,
        warnings=preflight.warnings,
        fallback_count=fallback_count,
    )
    calls = [first]
    if first.response.error is not None:
        return CandidateResult(first.response, calls)
    try:
        normalized = normalize_structured_response(first.response, prepared)
        return CandidateResult(normalized, calls)
    except ValidationError as initial_error:
        _mark_schema_failure(db, first.invocation, str(initial_error))
        if not prepared.repair_allowed:
            failed = schema_failure_response(
                request,
                first.response,
                str(initial_error),
                list(prepared.warnings),
            )
            return CandidateResult(failed, calls)

        repair_request = build_repair_request(
            prepared, first.response.text, str(initial_error)
        )
        repair_preflight_context = context_preflight(
            repair_request,
            candidate.profile.context_window,
            tokenizer_name=candidate.profile.tokenizer_name,
            tokenizer_source=candidate.profile.tokenizer_source,
        )
        repair_preflight = ExecutionPreflightRead(
            model_profile_id=candidate.profile.id,
            provider_account_id=candidate.provider.id,
            model_name=candidate.profile.name,
            context=repair_preflight_context,
            estimated_cost=preflight_cost(
                db, candidate.profile.id, repair_request, repair_preflight_context
            ),
            capabilities=capabilities_read,
            warnings=["结构化输出首次校验失败，正在执行唯一一次有限修复。"],
        )
        if repair_preflight_context.blocked:
            failed = schema_failure_response(
                request,
                first.response,
                "修复请求超过上下文窗口",
                list(prepared.warnings),
            )
            return CandidateResult(failed, calls)
        repair = await _call_once(
            db,
            payload,
            resolution,
            candidate,
            repair_request,
            repair_preflight,
            warnings=repair_preflight.warnings,
            fallback_count=fallback_count,
        )
        calls.append(repair)
        if repair.response.error is not None:
            failed = schema_failure_response(
                request,
                repair.response,
                repair.response.error.message,
                list(prepared.warnings),
            )
            return CandidateResult(failed, calls)
        try:
            repaired = normalize_structured_response(repair.response, prepared)
        except ValidationError as repair_error:
            _mark_schema_failure(db, repair.invocation, str(repair_error))
            failed = schema_failure_response(
                request,
                repair.response,
                str(repair_error),
                list(prepared.warnings),
            )
            failed.usage = _sum_usage(first.response.usage, repair.response.usage)
            return CandidateResult(failed, calls)
        repaired.usage = _sum_usage(first.response.usage, repair.response.usage)
        repaired.warnings = [
            *repaired.warnings,
            "结构化输出经过一次有限修复后通过本地 Schema 校验。",
        ]
        return CandidateResult(repaired, calls)


async def _call_once(
    db: Session,
    payload: ModelDebugRequest,
    resolution: RouteResolution,
    candidate: RouteCandidate,
    request: NormalizedModelRequest,
    preflight: ExecutionPreflightRead,
    *,
    warnings: list[str],
    fallback_count: int,
) -> CallResult:
    budget_reservation: BudgetReservation | None = None
    lease: RateLimitLease | None = None
    health: models.ProviderHealth | None = None
    internal_request_id = f"nas-{uuid.uuid4().hex}"
    invocation = models.ModelInvocation(
        request_id=internal_request_id,
        project_id=payload.project_id,
        provider_account_id=candidate.provider.id,
        model_profile_id=candidate.profile.id,
        route_id=resolution.route.id if resolution.route is not None else None,
        route_run_id=resolution.route_run_id,
        workflow_id=payload.workflow_id,
        status="queued",
        input_tokens=preflight.context.input.tokens,
        total_tokens=preflight.context.total_tokens,
        usage_estimated=True,
        token_source=preflight.context.input.source,
        cost=preflight.estimated_cost.amount,
        cost_known=preflight.estimated_cost.known,
        currency=preflight.estimated_cost.currency,
        fallback_count=fallback_count,
        warnings_json=json.dumps(warnings, ensure_ascii=False),
    )
    try:
        budget_reservation = await current_budget_manager().reserve(
            db,
            BudgetContext(
                project_id=payload.project_id,
                route_id=resolution.route.id if resolution.route is not None else None,
                route_run_id=resolution.route_run_id,
            ),
            tokens=preflight.context.total_tokens,
            cost=preflight.estimated_cost,
        )
        db.add(invocation)
        db.commit()
        lease = await current_rate_limiter().acquire(
            matching_rate_limits(
                db,
                LimitContext(
                    project_id=payload.project_id,
                    provider_id=candidate.provider.id,
                    model_id=candidate.profile.id,
                    route_id=resolution.route.id if resolution.route is not None else None,
                    workflow_id=payload.workflow_id,
                ),
            ),
            preflight.context.total_tokens,
        )
        invocation.status = "running"
        invocation.queue_ms = lease.queue_ms
        health = claim_provider(db, candidate.provider.id)
        db.commit()

        started = time.perf_counter()
        response: NormalizedModelResponse
        try:
            runtime = model_service.provider_runtime(db, candidate.provider.id)
            adapter = model_gateway.registry.get(runtime.protocol)
            response = await adapter.complete(request, runtime)
        except ProviderRequestError as exc:
            response = _control_error_response(request, exc.error)
        except KeyError:
            response = _control_error_response(
                request,
                NormalizedProviderError(
                    code="capability_unsupported",
                    message="当前协议没有注册适配器",
                    status_code=400,
                ),
            )
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        usage = normalize_usage(
            response.usage,
            request,
            response.text,
            tokenizer_name=candidate.profile.tokenizer_name,
            tokenizer_source=candidate.profile.tokenizer_source,
        )
        response.usage = usage
        response.warnings = list(dict.fromkeys([*warnings, *response.warnings]))
        pricing = active_pricing(db, candidate.profile.id)
        actual_cost = estimate_cost(pricing, usage, tool_calls=len(response.tool_calls))
        if health is not None:
            record_provider_result(health, error=response.error, latency_ms=latency_ms)
        _finalize_invocation(
            invocation,
            response,
            actual_cost.model_dump(mode="json"),
            latency_ms,
            warnings,
        )
        db.commit()
        return CallResult(
            response=response,
            invocation=invocation,
            preflight=preflight,
            actual_cost=actual_cost.model_dump(mode="json"),
        )
    except asyncio.CancelledError:
        error = NormalizedProviderError(
            code="cancelled", message="用户取消了模型请求", status_code=499
        )
        if health is not None:
            record_provider_result(health, error=error, latency_ms=0)
        invocation.status = "cancelled"
        invocation.error_code = "cancelled"
        invocation.completed_at = models.utcnow()
        if invocation.id:
            db.commit()
        raise
    except ModelControlError as exc:
        response = _control_error_response(request, exc.error)
        if invocation.id:
            _finalize_invocation(
                invocation,
                response,
                preflight.estimated_cost.model_dump(mode="json"),
                0,
                warnings,
            )
            db.commit()
        return CallResult(
            response=response,
            invocation=invocation,
            preflight=preflight,
            actual_cost=preflight.estimated_cost.model_dump(mode="json"),
        )
    finally:
        if lease is not None:
            consumed = invocation.total_tokens if invocation.status == "completed" else 0
            await lease.release(consumed)
        if budget_reservation is not None:
            await budget_reservation.release()


async def _stream_candidate(
    db: Session,
    payload: ModelDebugRequest,
    resolution: RouteResolution,
    candidate: RouteCandidate,
    prepared: PreparedRequest,
    preflight: ExecutionPreflightRead,
    *,
    fallback_count: int,
) -> AsyncIterator[NormalizedStreamEvent]:
    if preflight.context.blocked:
        raise ModelControlError(
            "context_too_long", "Context Preflight 已阻止流式请求", status_code=422
        )
    budget: BudgetReservation | None = None
    lease: RateLimitLease | None = None
    health: models.ProviderHealth | None = None
    invocation = models.ModelInvocation(
        request_id=f"nas-{uuid.uuid4().hex}",
        project_id=payload.project_id,
        provider_account_id=candidate.provider.id,
        model_profile_id=candidate.profile.id,
        route_id=resolution.route.id if resolution.route is not None else None,
        route_run_id=resolution.route_run_id,
        workflow_id=payload.workflow_id,
        status="queued",
        input_tokens=preflight.context.input.tokens,
        total_tokens=preflight.context.total_tokens,
        usage_estimated=True,
        token_source=preflight.context.input.source,
        cost=preflight.estimated_cost.amount,
        cost_known=preflight.estimated_cost.known,
        currency=preflight.estimated_cost.currency,
        fallback_count=fallback_count,
        warnings_json=json.dumps(preflight.warnings, ensure_ascii=False),
    )
    text = ""
    usage = NormalizedUsage()
    upstream_error: NormalizedProviderError | None = None
    latency_ms = 0
    request_id = ""
    finish_reason = "stop"
    buffered = prepared.structured_mode != "none"
    saw_delta = False
    try:
        budget = await current_budget_manager().reserve(
            db,
            BudgetContext(
                payload.project_id,
                resolution.route.id if resolution.route is not None else None,
                resolution.route_run_id,
            ),
            tokens=preflight.context.total_tokens,
            cost=preflight.estimated_cost,
        )
        db.add(invocation)
        db.commit()
        lease = await current_rate_limiter().acquire(
            matching_rate_limits(
                db,
                LimitContext(
                    payload.project_id,
                    candidate.provider.id,
                    candidate.profile.id,
                    resolution.route.id if resolution.route is not None else None,
                    payload.workflow_id,
                ),
            ),
            preflight.context.total_tokens,
        )
        invocation.status = "running"
        invocation.queue_ms = lease.queue_ms
        health = claim_provider(db, candidate.provider.id)
        db.commit()
        runtime = model_service.provider_runtime(db, candidate.provider.id)
        adapter = model_gateway.registry.get(runtime.protocol)
        started = time.perf_counter()
        yield NormalizedStreamEvent(sequence=0, event="start")
        for warning in preflight.warnings:
            yield NormalizedStreamEvent(sequence=0, event="warning", warning=warning)
        try:
            async for event in adapter.stream(prepared.request, runtime):
                request_id = event.request_id or request_id
                if event.event == "delta":
                    text += event.text_delta
                    saw_delta = saw_delta or bool(event.text_delta)
                    if not buffered:
                        yield event
                elif event.event == "usage" and event.usage is not None:
                    usage = event.usage
                elif event.event == "error" and event.error is not None:
                    upstream_error = event.error
                    if saw_delta:
                        yield event
                elif event.event == "done":
                    finish_reason = event.finish_reason or finish_reason
                elif event.event != "start":
                    yield event
        except ProviderRequestError as exc:
            upstream_error = exc.error
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        usage = normalize_usage(
            usage,
            prepared.request,
            text,
            tokenizer_name=candidate.profile.tokenizer_name,
            tokenizer_source=candidate.profile.tokenizer_source,
        )
        response = NormalizedModelResponse(
            model=candidate.profile.name,
            text=text,
            content=[NormalizedContentPart(type="text", text=text)] if text else [],
            finish_reason="error" if upstream_error else finish_reason,
            usage=usage,
            request_id=request_id,
            error=upstream_error,
            warnings=list(prepared.warnings),
        )
        if upstream_error is None and buffered:
            try:
                response = normalize_structured_response(response, prepared)
            except ValidationError as exc:
                response = schema_failure_response(
                    prepared.request,
                    response,
                    str(exc),
                    list(prepared.warnings),
                )
                upstream_error = response.error
            if response.error is None:
                yield NormalizedStreamEvent(
                    sequence=0,
                    event="delta",
                    text_delta=response.text,
                    request_id=response.request_id,
                )
            else:
                yield NormalizedStreamEvent(
                    sequence=0,
                    event="error",
                    error=response.error,
                    request_id=response.request_id,
                )
        actual_cost = estimate_cost(
            active_pricing(db, candidate.profile.id),
            usage,
            tool_calls=len(response.tool_calls),
        )
        if health is not None:
            record_provider_result(health, error=upstream_error, latency_ms=latency_ms)
        _finalize_invocation(
            invocation,
            response,
            actual_cost.model_dump(mode="json"),
            latency_ms,
            preflight.warnings,
        )
        db.commit()
        if upstream_error is not None and not saw_delta:
            yield NormalizedStreamEvent(sequence=0, event="error", error=upstream_error)
            return
        yield NormalizedStreamEvent(
            sequence=0, event="usage", usage=usage, request_id=request_id
        )
        yield NormalizedStreamEvent(
            sequence=0,
            event="done",
            finish_reason=response.finish_reason,
            request_id=request_id,
        )
    except asyncio.CancelledError:
        invocation.status = "cancelled"
        invocation.error_code = "cancelled"
        invocation.completed_at = models.utcnow()
        if health is not None:
            record_provider_result(
                health,
                error=NormalizedProviderError(code="cancelled", message="用户取消"),
                latency_ms=latency_ms,
            )
        if invocation.id:
            db.commit()
        raise
    except (ProviderRequestError, KeyError) as exc:
        error = (
            exc.error
            if isinstance(exc, ProviderRequestError)
            else NormalizedProviderError(
                code="capability_unsupported", message="当前协议没有注册适配器"
            )
        )
        response = _control_error_response(prepared.request, error)
        if health is not None:
            record_provider_result(health, error=error, latency_ms=latency_ms)
        if invocation.id:
            _finalize_invocation(
                invocation,
                response,
                preflight.estimated_cost.model_dump(mode="json"),
                latency_ms,
                preflight.warnings,
            )
            db.commit()
        yield NormalizedStreamEvent(sequence=0, event="error", error=error)
    finally:
        if lease is not None:
            await lease.release(invocation.total_tokens if invocation.status == "completed" else 0)
        if budget is not None:
            await budget.release()


def _finalize_invocation(
    invocation: models.ModelInvocation,
    response: NormalizedModelResponse,
    cost: dict[str, Any],
    latency_ms: int,
    warnings: list[str],
) -> None:
    usage = response.usage
    invocation.status = "completed" if response.error is None else "failed"
    invocation.input_tokens = usage.input_tokens
    invocation.output_tokens = usage.output_tokens
    invocation.cached_input_tokens = usage.cached_input_tokens
    invocation.reasoning_tokens = usage.reasoning_tokens
    invocation.total_tokens = usage.total_tokens
    invocation.usage_estimated = usage.estimated
    invocation.token_source = usage.source
    invocation.cost = cost.get("amount")
    invocation.cost_known = bool(cost.get("known"))
    invocation.currency = str(cost.get("currency") or "USD")
    invocation.latency_ms = latency_ms
    invocation.error_code = response.error.code if response.error is not None else None
    invocation.warnings_json = json.dumps(
        list(dict.fromkeys([*warnings, *response.warnings])), ensure_ascii=False
    )
    invocation.completed_at = models.utcnow()


def _mark_schema_failure(
    db: Session, invocation: models.ModelInvocation, message: str
) -> None:
    invocation.status = "failed"
    invocation.error_code = "schema_validation"
    try:
        warnings = json.loads(invocation.warnings_json)
    except json.JSONDecodeError:
        warnings = []
    if not isinstance(warnings, list):
        warnings = []
    warnings.append(f"本地 Schema 校验失败：{message[:500]}")
    invocation.warnings_json = json.dumps(warnings, ensure_ascii=False)
    db.commit()


def _attach_control(
    response: NormalizedModelResponse,
    resolution: RouteResolution,
    candidate: RouteCandidate,
    calls: list[CallResult],
    fallback_count: int,
    warnings: list[str],
) -> NormalizedModelResponse:
    response.warnings = list(dict.fromkeys([*warnings, *response.warnings]))
    response.control = {
        "selected_model_profile_id": candidate.profile.id,
        "selected_provider_account_id": candidate.provider.id,
        "route_id": resolution.route.id if resolution.route is not None else None,
        "route_run_id": resolution.route_run_id,
        "fallback_count": fallback_count,
        "queue_ms": sum(item.invocation.queue_ms or 0 for item in calls),
        "invocation_ids": [item.invocation.id for item in calls if item.invocation.id],
        "attempts": [
            {
                "invocation_id": item.invocation.id,
                "model_profile_id": item.invocation.model_profile_id,
                "status": item.invocation.status,
                "cost": item.actual_cost,
                "preflight": item.preflight.context.model_dump(mode="json"),
            }
            for item in calls
        ],
    }
    return response


def _control_error_response(
    request: NormalizedModelRequest, error: NormalizedProviderError
) -> NormalizedModelResponse:
    return NormalizedModelResponse(
        model=request.model,
        text="",
        usage=NormalizedUsage(),
        request_id=error.request_id or "",
        finish_reason="error",
        error=error,
    )


def _sum_usage(left: NormalizedUsage, right: NormalizedUsage) -> NormalizedUsage:
    estimated = left.estimated or right.estimated
    source = (
        "provider_actual"
        if not estimated
        else (
            left.source
            if left.source == right.source
            else "local_approximation"
        )
    )
    return NormalizedUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        cached_input_tokens=left.cached_input_tokens + right.cached_input_tokens,
        reasoning_tokens=left.reasoning_tokens + right.reasoning_tokens,
        estimated=estimated,
        source=cast(Any, source),
    )
