from __future__ import annotations

import asyncio
import json
import re
import uuid
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
import tiktoken

from app import models
from app.schemas import (
    ContextPreflightRead,
    CostEstimateRead,
    NormalizedModelRequest,
    NormalizedUsage,
    TokenEstimateRead,
)
from app.services.control_errors import ModelControlError
from app.services.model_control import start_of_utc_day


TOKEN_SOURCE_PRIORITY = {
    "local_approximation": 0,
    "compatible_tokenizer": 1,
    "official_tokenizer": 2,
    "provider_estimate": 3,
    "provider_actual": 4,
}


def estimate_input(
    request: NormalizedModelRequest,
    *,
    tokenizer_name: str | None = None,
    tokenizer_source: str | None = None,
) -> TokenEstimateRead:
    parts: list[str] = []
    for message in request.messages:
        parts.append(message.role)
        for content in message.content:
            if content.text:
                parts.append(content.text)
            if content.data is not None:
                parts.append(json.dumps(content.data, ensure_ascii=False, sort_keys=True))
            if content.arguments is not None:
                parts.append(
                    json.dumps(content.arguments, ensure_ascii=False, sort_keys=True)
                    if not isinstance(content.arguments, str)
                    else content.arguments
                )
    if request.json_schema is not None:
        parts.append(json.dumps(request.json_schema, ensure_ascii=False, sort_keys=True))
    for tool in request.tools:
        parts.extend(
            [
                tool.name,
                tool.description,
                json.dumps(tool.input_schema, ensure_ascii=False, sort_keys=True),
            ]
        )
    text = "\n".join(parts)
    if tokenizer_name is not None and tokenizer_source is not None:
        if tokenizer_source not in {"official_tokenizer", "compatible_tokenizer"}:
            raise ModelControlError(
                "invalid_tokenizer_source",
                "Tokenizer 来源必须是 official_tokenizer 或 compatible_tokenizer",
                status_code=422,
            )
        try:
            encoding = tiktoken.get_encoding(tokenizer_name)
        except ValueError as exc:
            raise ModelControlError(
                "tokenizer_unavailable",
                f"找不到配置的 tokenizer：{tokenizer_name}",
                status_code=409,
            ) from exc
        return TokenEstimateRead(
            tokens=len(encoding.encode(text, disallowed_special=())),
            estimated=True,
            source=cast(Any, tokenizer_source),
        )
    return TokenEstimateRead(
        tokens=_local_token_estimate(text), estimated=True, source="local_approximation"
    )


def context_preflight(
    request: NormalizedModelRequest,
    context_window: int,
    *,
    tokenizer_name: str | None = None,
    tokenizer_source: str | None = None,
) -> ContextPreflightRead:
    input_estimate = estimate_input(
        request,
        tokenizer_name=tokenizer_name,
        tokenizer_source=tokenizer_source,
    )
    reserved = request.max_tokens
    total = input_estimate.tokens + reserved
    remaining = context_window - total
    utilization = total / context_window if context_window > 0 else 1.0
    warnings: list[str] = []
    if utilization >= 1:
        level = "blocked"
        warnings.append("预计上下文达到或超过 100%，请求已阻止。")
    elif utilization >= 0.95:
        level = "strong_warning"
        warnings.append("预计上下文已达到 95%，极易超出模型窗口。")
    elif utilization >= 0.8:
        level = "warning"
        warnings.append("预计上下文已达到 80%，建议缩短输入或保留输出。")
    else:
        level = "ok"
    return ContextPreflightRead(
        input=input_estimate,
        reserved_output_tokens=reserved,
        total_tokens=total,
        context_window=context_window,
        remaining_tokens=remaining,
        utilization=round(utilization, 6),
        level=cast(Any, level),
        blocked=utilization >= 1,
        warnings=warnings,
    )


def active_pricing(
    db: Session, model_profile_id: int, at: datetime | None = None
) -> models.ModelPricing | None:
    moment = at or datetime.now(timezone.utc)
    return db.scalar(
        select(models.ModelPricing)
        .where(
            models.ModelPricing.model_profile_id == model_profile_id,
            models.ModelPricing.deleted_at.is_(None),
            models.ModelPricing.effective_from <= moment,
            (
                models.ModelPricing.effective_to.is_(None)
                | (models.ModelPricing.effective_to > moment)
            ),
        )
        .order_by(models.ModelPricing.effective_from.desc(), models.ModelPricing.id.desc())
    )


def estimate_cost(
    pricing: models.ModelPricing | None,
    usage: NormalizedUsage,
    *,
    tool_calls: int = 0,
) -> CostEstimateRead:
    if pricing is None:
        return CostEstimateRead(
            known=False,
            amount=None,
            currency="USD",
            breakdown={
                "input": None,
                "cached_input": None,
                "output": None,
                "reasoning": None,
                "request": None,
                "tools": None,
            },
            pricing_id=None,
            reason="当前时间没有生效的价格记录",
        )

    uncached_input = max(0, usage.input_tokens - usage.cached_input_tokens)
    components: list[tuple[str, int, float | None, float]] = [
        ("input", uncached_input, pricing.input_per_million, 1_000_000),
        (
            "cached_input",
            usage.cached_input_tokens,
            pricing.cached_input_per_million,
            1_000_000,
        ),
        ("output", usage.output_tokens, pricing.output_per_million, 1_000_000),
        (
            "reasoning",
            usage.reasoning_tokens,
            pricing.reasoning_per_million,
            1_000_000,
        ),
        ("request", 1, pricing.request_fee, 1),
        ("tools", tool_calls, pricing.tool_call_fee, 1),
    ]
    breakdown: dict[str, float | None] = {}
    missing: list[str] = []
    total = 0.0
    for name, units, rate, divisor in components:
        if units == 0:
            breakdown[name] = 0.0
            continue
        if rate is None:
            breakdown[name] = None
            missing.append(name)
            continue
        value = units / divisor * rate
        breakdown[name] = value
        total += value
    return CostEstimateRead(
        known=not missing,
        amount=round(total, 12) if not missing else None,
        currency=pricing.currency,
        breakdown=breakdown,
        pricing_id=pricing.id,
        reason=(f"以下价格未知：{', '.join(missing)}" if missing else None),
    )


def preflight_cost(
    db: Session,
    model_profile_id: int,
    request: NormalizedModelRequest,
    context: ContextPreflightRead,
) -> CostEstimateRead:
    usage = NormalizedUsage(
        input_tokens=context.input.tokens,
        output_tokens=request.max_tokens,
        total_tokens=context.total_tokens,
        estimated=True,
        source=context.input.source,
    )
    return estimate_cost(
        active_pricing(db, model_profile_id), usage, tool_calls=len(request.tools)
    )


def normalize_usage(
    usage: NormalizedUsage,
    request: NormalizedModelRequest,
    output: str,
    *,
    tokenizer_name: str | None = None,
    tokenizer_source: str | None = None,
) -> NormalizedUsage:
    if usage.total_tokens > 0:
        source = usage.source
        if usage.estimated and source == "provider_actual":
            source = "provider_estimate"
        if not usage.estimated:
            source = "provider_actual"
        return usage.model_copy(update={"source": source})
    input_count = estimate_input(
        request,
        tokenizer_name=tokenizer_name,
        tokenizer_source=tokenizer_source,
    )
    if tokenizer_name is not None and tokenizer_source is not None:
        try:
            encoding = tiktoken.get_encoding(tokenizer_name)
        except ValueError as exc:
            raise ModelControlError(
                "tokenizer_unavailable",
                f"找不到配置的 tokenizer：{tokenizer_name}",
                status_code=409,
            ) from exc
        output_tokens = len(encoding.encode(output, disallowed_special=()))
    else:
        output_tokens = _local_token_estimate(output)
    return NormalizedUsage(
        input_tokens=input_count.tokens,
        output_tokens=output_tokens,
        total_tokens=input_count.tokens + output_tokens,
        estimated=True,
        source=input_count.source,
    )


@dataclass(frozen=True)
class BudgetContext:
    project_id: int | None
    route_id: int | None
    route_run_id: str | None


@dataclass
class BudgetReservation:
    manager: BudgetManager
    reservation_id: str
    policy_keys: list[tuple[int, str]]
    released: bool = False

    async def release(self) -> None:
        if self.released:
            return
        self.released = True
        await self.manager.release(self)


class BudgetManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: dict[tuple[int, str], dict[str, tuple[int, float | None, str]]] = {}

    async def reserve(
        self,
        db: Session,
        context: BudgetContext,
        *,
        tokens: int,
        cost: CostEstimateRead,
    ) -> BudgetReservation:
        policies = _matching_budget_policies(db, context)
        reservation_id = uuid.uuid4().hex
        policy_keys: list[tuple[int, str]] = []
        async with self._lock:
            for policy, window_key in policies:
                key = (policy.id, window_key)
                pending = self._pending.get(key, {})
                _check_budget_policy(
                    db,
                    policy,
                    context,
                    window_key,
                    tokens,
                    cost,
                    list(pending.values()),
                )
                policy_keys.append(key)
            for key in policy_keys:
                self._pending.setdefault(key, {})[reservation_id] = (
                    tokens,
                    cost.amount if cost.known else None,
                    cost.currency,
                )
        return BudgetReservation(self, reservation_id, policy_keys)

    async def release(self, reservation: BudgetReservation) -> None:
        async with self._lock:
            for key in reservation.policy_keys:
                entries = self._pending.get(key)
                if entries is None:
                    continue
                entries.pop(reservation.reservation_id, None)
                if not entries:
                    self._pending.pop(key, None)


_budget_managers: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, BudgetManager
] = weakref.WeakKeyDictionary()


def current_budget_manager() -> BudgetManager:
    loop = asyncio.get_running_loop()
    manager = _budget_managers.get(loop)
    if manager is None:
        manager = BudgetManager()
        _budget_managers[loop] = manager
    return manager


def _matching_budget_policies(
    db: Session, context: BudgetContext
) -> list[tuple[models.BudgetPolicy, str]]:
    rows = db.scalars(
        select(models.BudgetPolicy).where(
            models.BudgetPolicy.enabled.is_(True),
            models.BudgetPolicy.deleted_at.is_(None),
        )
    ).all()
    matched: list[tuple[models.BudgetPolicy, str]] = []
    for policy in rows:
        if policy.scope_type == "per_request" and policy.scope_key == "*":
            matched.append((policy, "request"))
        elif (
            policy.scope_type == "project_daily"
            and context.project_id is not None
            and policy.scope_key == str(context.project_id)
        ):
            matched.append((policy, start_of_utc_day().isoformat()))
        elif (
            policy.scope_type == "route_per_run"
            and context.route_id is not None
            and policy.scope_key == str(context.route_id)
        ):
            matched.append((policy, context.route_run_id or "single-request"))
    return matched


def _check_budget_policy(
    db: Session,
    policy: models.BudgetPolicy,
    context: BudgetContext,
    window_key: str,
    requested_tokens: int,
    requested_cost: CostEstimateRead,
    pending: list[tuple[int, float | None, str]],
) -> None:
    used_tokens = 0
    used_cost = 0.0
    unknown_cost_exists = False
    if policy.scope_type != "per_request":
        stmt = select(
            func.coalesce(func.sum(models.ModelInvocation.total_tokens), 0),
            func.coalesce(func.sum(models.ModelInvocation.cost), 0.0),
            func.coalesce(
                func.sum(
                    case(
                        (models.ModelInvocation.cost_known.is_(False), 1),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(models.ModelInvocation.status.in_(["completed", "failed", "cancelled"]))
        if policy.scope_type == "project_daily":
            stmt = stmt.where(
                models.ModelInvocation.project_id == context.project_id,
                models.ModelInvocation.started_at >= start_of_utc_day(),
            )
        else:
            stmt = stmt.where(
                models.ModelInvocation.route_id == context.route_id,
                models.ModelInvocation.route_run_id == context.route_run_id,
            )
        used_tokens_raw, used_cost_raw, unknown_raw = db.execute(stmt).one()
        used_tokens = int(used_tokens_raw or 0)
        used_cost = float(used_cost_raw or 0)
        unknown_cost_exists = bool(unknown_raw)

    pending_tokens = sum(item[0] for item in pending)
    pending_cost_values = [item[1] for item in pending]
    pending_cost_unknown = any(value is None for value in pending_cost_values)
    pending_cost = sum(value or 0 for value in pending_cost_values)

    if (
        policy.max_tokens is not None
        and used_tokens + pending_tokens + requested_tokens > policy.max_tokens
    ):
        raise ModelControlError(
            "budget_exceeded",
            f"{policy.scope_type} Token 预算不足",
            status_code=409,
        )
    if policy.max_cost is None:
        return
    if requested_cost.currency != policy.currency:
        raise ModelControlError(
            "budget_currency_mismatch",
            "价格币种与预算币种不一致，无法安全换算",
            status_code=409,
        )
    if not requested_cost.known or unknown_cost_exists or pending_cost_unknown:
        raise ModelControlError(
            "budget_unknown_cost",
            "存在未知价格或未知历史费用，不能把它按 0 计入预算",
            status_code=409,
        )
    if used_cost + pending_cost + (requested_cost.amount or 0) > policy.max_cost:
        raise ModelControlError(
            "budget_exceeded",
            f"{policy.scope_type} 费用预算不足",
            status_code=409,
        )


def _local_token_estimate(text: str) -> int:
    if not text:
        return 0
    cjk_count = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text))
    non_cjk = re.sub(r"[\u3400-\u9fff\uf900-\ufaff]", "", text)
    ascii_units = max(0, (len(non_cjk.encode("utf-8")) + 3) // 4)
    return max(1, cjk_count + ascii_units)


def estimate_text_tokens(text: str) -> int:
    """Return the deterministic local estimate used by preflight and ContextBuilder."""
    return _local_token_estimate(text)
