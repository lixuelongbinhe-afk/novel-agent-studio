from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404, require_revision, soft_delete
from app.schemas import (
    BudgetPolicyRead,
    BudgetPolicyUpdate,
    BudgetPolicyWrite,
    ModelInvocationRead,
    ModelPricingRead,
    ModelPricingWrite,
    ModelRouteRead,
    ModelRouteUpdate,
    ModelRouteWrite,
    ProviderHealthRead,
    RateLimitPolicyRead,
    RateLimitPolicyUpdate,
    RateLimitPolicyWrite,
    RouteEntryRead,
)


def list_pricing(db: Session, model_profile_id: int) -> list[ModelPricingRead]:
    get_or_404(db, models.ModelProfile, model_profile_id)
    rows = db.scalars(
        select(models.ModelPricing)
        .where(
            models.ModelPricing.model_profile_id == model_profile_id,
            models.ModelPricing.deleted_at.is_(None),
        )
        .order_by(models.ModelPricing.effective_from.desc(), models.ModelPricing.id.desc())
    ).all()
    return [ModelPricingRead.model_validate(row) for row in rows]


def create_pricing(
    db: Session, model_profile_id: int, payload: ModelPricingWrite
) -> ModelPricingRead:
    get_or_404(db, models.ModelProfile, model_profile_id)
    overlap_stmt = select(models.ModelPricing).where(
        models.ModelPricing.model_profile_id == model_profile_id,
        models.ModelPricing.deleted_at.is_(None),
        (
            models.ModelPricing.effective_to.is_(None)
            | (models.ModelPricing.effective_to > payload.effective_from)
        ),
    )
    if payload.effective_to is not None:
        overlap_stmt = overlap_stmt.where(
            models.ModelPricing.effective_from < payload.effective_to
        )
    overlapping = db.scalar(overlap_stmt)
    if overlapping is not None:
        raise HTTPException(status_code=409, detail="价格生效区间不能重叠")
    row = models.ModelPricing(model_profile_id=model_profile_id, **payload.model_dump())
    db.add(row)
    db.flush()
    return ModelPricingRead.model_validate(row)


def delete_pricing(
    db: Session, pricing_id: int, expected_revision: int
) -> None:
    row = cast(models.ModelPricing, get_or_404(db, models.ModelPricing, pricing_id))
    require_revision(row, expected_revision)
    soft_delete(row)
    db.flush()


def list_routes(db: Session, project_id: int | None = None) -> list[ModelRouteRead]:
    stmt = select(models.ModelRoute).where(models.ModelRoute.deleted_at.is_(None))
    if project_id is not None:
        stmt = stmt.where(
            (models.ModelRoute.project_id == project_id)
            | (models.ModelRoute.project_id.is_(None))
        )
    rows = db.scalars(stmt.order_by(models.ModelRoute.id)).all()
    return [_route_read(db, row) for row in rows]


def create_route(db: Session, payload: ModelRouteWrite) -> ModelRouteRead:
    _validate_route_references(db, payload)
    row = models.ModelRoute(
        project_id=payload.project_id,
        name=payload.name,
        strategy=payload.strategy,
        required_capabilities_json=json.dumps(
            payload.required_capabilities, ensure_ascii=True
        ),
        allow_degradation=payload.allow_degradation,
        enabled=payload.enabled,
    )
    db.add(row)
    db.flush()
    _replace_route_entries(db, row.id, payload.entries)
    db.flush()
    return _route_read(db, row)


def update_route(
    db: Session, route_id: int, payload: ModelRouteUpdate
) -> ModelRouteRead:
    row = cast(models.ModelRoute, get_or_404(db, models.ModelRoute, route_id))
    require_revision(row, payload.expected_revision)
    _validate_route_references(db, payload)
    row.project_id = payload.project_id
    row.name = payload.name
    row.strategy = payload.strategy
    row.required_capabilities_json = json.dumps(
        payload.required_capabilities, ensure_ascii=True
    )
    row.allow_degradation = payload.allow_degradation
    row.enabled = payload.enabled
    row.revision += 1
    _replace_route_entries(db, row.id, payload.entries)
    db.flush()
    return _route_read(db, row)


def delete_route(db: Session, route_id: int, expected_revision: int) -> None:
    row = cast(models.ModelRoute, get_or_404(db, models.ModelRoute, route_id))
    require_revision(row, expected_revision)
    soft_delete(row)
    entries = db.scalars(
        select(models.ModelRouteEntry).where(
            models.ModelRouteEntry.route_id == route_id,
            models.ModelRouteEntry.deleted_at.is_(None),
        )
    ).all()
    for entry in entries:
        soft_delete(entry)
    db.flush()


def list_rate_limits(db: Session) -> list[RateLimitPolicyRead]:
    rows = db.scalars(
        select(models.RateLimitPolicy)
        .where(models.RateLimitPolicy.deleted_at.is_(None))
        .order_by(models.RateLimitPolicy.scope_type, models.RateLimitPolicy.scope_key)
    ).all()
    return [RateLimitPolicyRead.model_validate(row) for row in rows]


def create_rate_limit(
    db: Session, payload: RateLimitPolicyWrite
) -> RateLimitPolicyRead:
    _validate_limit_scope(db, payload.scope_type, payload.scope_key)
    existing = db.scalar(
        select(models.RateLimitPolicy).where(
            models.RateLimitPolicy.scope_type == payload.scope_type,
            models.RateLimitPolicy.scope_key == payload.scope_key,
        )
    )
    if existing is not None and existing.deleted_at is None:
        raise HTTPException(status_code=409, detail="该限流范围已存在")
    if existing is None:
        row = models.RateLimitPolicy(**payload.model_dump())
        db.add(row)
    else:
        row = existing
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
        row.deleted_at = None
        row.revision += 1
    db.flush()
    return RateLimitPolicyRead.model_validate(row)


def update_rate_limit(
    db: Session, policy_id: int, payload: RateLimitPolicyUpdate
) -> RateLimitPolicyRead:
    row = cast(
        models.RateLimitPolicy, get_or_404(db, models.RateLimitPolicy, policy_id)
    )
    require_revision(row, payload.expected_revision)
    _validate_limit_scope(db, payload.scope_type, payload.scope_key)
    duplicate = db.scalar(
        select(models.RateLimitPolicy).where(
            models.RateLimitPolicy.scope_type == payload.scope_type,
            models.RateLimitPolicy.scope_key == payload.scope_key,
            models.RateLimitPolicy.id != row.id,
            models.RateLimitPolicy.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该限流范围已存在")
    for key, value in payload.model_dump(exclude={"expected_revision"}).items():
        setattr(row, key, value)
    row.revision += 1
    db.flush()
    return RateLimitPolicyRead.model_validate(row)


def delete_rate_limit(db: Session, policy_id: int, expected_revision: int) -> None:
    row = cast(
        models.RateLimitPolicy, get_or_404(db, models.RateLimitPolicy, policy_id)
    )
    require_revision(row, expected_revision)
    soft_delete(row)
    db.flush()


def list_budgets(db: Session) -> list[BudgetPolicyRead]:
    rows = db.scalars(
        select(models.BudgetPolicy)
        .where(models.BudgetPolicy.deleted_at.is_(None))
        .order_by(models.BudgetPolicy.scope_type, models.BudgetPolicy.scope_key)
    ).all()
    return [BudgetPolicyRead.model_validate(row) for row in rows]


def create_budget(db: Session, payload: BudgetPolicyWrite) -> BudgetPolicyRead:
    _validate_budget_scope(db, payload.scope_type, payload.scope_key)
    existing = db.scalar(
        select(models.BudgetPolicy).where(
            models.BudgetPolicy.scope_type == payload.scope_type,
            models.BudgetPolicy.scope_key == payload.scope_key,
        )
    )
    if existing is not None and existing.deleted_at is None:
        raise HTTPException(status_code=409, detail="该预算范围已存在")
    if existing is None:
        row = models.BudgetPolicy(**payload.model_dump())
        db.add(row)
    else:
        row = existing
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
        row.deleted_at = None
        row.revision += 1
    db.flush()
    return BudgetPolicyRead.model_validate(row)


def update_budget(
    db: Session, policy_id: int, payload: BudgetPolicyUpdate
) -> BudgetPolicyRead:
    row = cast(models.BudgetPolicy, get_or_404(db, models.BudgetPolicy, policy_id))
    require_revision(row, payload.expected_revision)
    _validate_budget_scope(db, payload.scope_type, payload.scope_key)
    duplicate = db.scalar(
        select(models.BudgetPolicy).where(
            models.BudgetPolicy.scope_type == payload.scope_type,
            models.BudgetPolicy.scope_key == payload.scope_key,
            models.BudgetPolicy.id != row.id,
            models.BudgetPolicy.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该预算范围已存在")
    for key, value in payload.model_dump(exclude={"expected_revision"}).items():
        setattr(row, key, value)
    row.revision += 1
    db.flush()
    return BudgetPolicyRead.model_validate(row)


def delete_budget(db: Session, policy_id: int, expected_revision: int) -> None:
    row = cast(models.BudgetPolicy, get_or_404(db, models.BudgetPolicy, policy_id))
    require_revision(row, expected_revision)
    soft_delete(row)
    db.flush()


def list_provider_health(db: Session) -> list[ProviderHealthRead]:
    providers = db.scalars(
        select(models.ProviderAccount).where(models.ProviderAccount.deleted_at.is_(None))
    ).all()
    values: list[ProviderHealthRead] = []
    for provider in providers:
        row = ensure_provider_health(db, provider.id)
        values.append(ProviderHealthRead.model_validate(row))
    db.flush()
    return values


def ensure_provider_health(db: Session, provider_id: int) -> models.ProviderHealth:
    get_or_404(db, models.ProviderAccount, provider_id)
    row = db.scalar(
        select(models.ProviderHealth).where(
            models.ProviderHealth.provider_account_id == provider_id,
            models.ProviderHealth.deleted_at.is_(None),
        )
    )
    if row is None:
        row = models.ProviderHealth(provider_account_id=provider_id)
        db.add(row)
        db.flush()
    return row


def reset_provider_health(db: Session, provider_id: int) -> ProviderHealthRead:
    row = ensure_provider_health(db, provider_id)
    row.state = "closed"
    row.consecutive_failures = 0
    row.half_open_in_flight = False
    row.opened_at = None
    row.last_error_code = None
    row.revision += 1
    db.flush()
    return ProviderHealthRead.model_validate(row)


def list_invocations(
    db: Session,
    *,
    project_id: int | None = None,
    route_id: int | None = None,
    limit: int = 100,
) -> list[ModelInvocationRead]:
    stmt = select(models.ModelInvocation)
    if project_id is not None:
        stmt = stmt.where(models.ModelInvocation.project_id == project_id)
    if route_id is not None:
        stmt = stmt.where(models.ModelInvocation.route_id == route_id)
    rows = db.scalars(
        stmt.order_by(models.ModelInvocation.id.desc()).limit(min(max(limit, 1), 500))
    ).all()
    return [ModelInvocationRead.model_validate(row) for row in rows]


def _route_read(db: Session, row: models.ModelRoute) -> ModelRouteRead:
    entries = db.scalars(
        select(models.ModelRouteEntry)
        .where(
            models.ModelRouteEntry.route_id == row.id,
            models.ModelRouteEntry.deleted_at.is_(None),
        )
        .order_by(models.ModelRouteEntry.position, models.ModelRouteEntry.id)
    ).all()
    try:
        raw = json.loads(row.required_capabilities_json)
    except json.JSONDecodeError:
        raw = []
    capabilities = [str(item) for item in raw] if isinstance(raw, list) else []
    return ModelRouteRead(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        strategy=cast(Any, row.strategy),
        required_capabilities=capabilities,
        allow_degradation=row.allow_degradation,
        enabled=row.enabled,
        revision=row.revision,
        entries=[RouteEntryRead.model_validate(entry) for entry in entries],
    )


def _validate_route_references(
    db: Session, payload: ModelRouteWrite | ModelRouteUpdate
) -> None:
    if payload.project_id is not None:
        get_or_404(db, models.Project, payload.project_id)
    for entry in payload.entries:
        profile = cast(
            models.ModelProfile,
            get_or_404(db, models.ModelProfile, entry.model_profile_id),
        )
        if profile.deleted_at is not None:
            raise HTTPException(status_code=409, detail="Route 包含已删除模型")


def _replace_route_entries(
    db: Session, route_id: int, entries: list[Any]
) -> None:
    existing = {
        row.model_profile_id: row
        for row in db.scalars(
            select(models.ModelRouteEntry).where(models.ModelRouteEntry.route_id == route_id)
        ).all()
    }
    active_ids: set[int] = set()
    for item in entries:
        active_ids.add(item.model_profile_id)
        row = existing.get(item.model_profile_id)
        if row is None:
            db.add(
                models.ModelRouteEntry(
                    route_id=route_id,
                    model_profile_id=item.model_profile_id,
                    position=item.position,
                    enabled=item.enabled,
                )
            )
            continue
        row.position = item.position
        row.enabled = item.enabled
        row.deleted_at = None
        row.revision += 1
    for model_id, row in existing.items():
        if model_id not in active_ids and row.deleted_at is None:
            soft_delete(row)


def _validate_limit_scope(db: Session, scope_type: str, scope_key: str) -> None:
    if scope_type == "global":
        return
    if scope_type == "workflow":
        return
    model_by_scope = {
        "project": models.Project,
        "provider": models.ProviderAccount,
        "model": models.ModelProfile,
        "route": models.ModelRoute,
    }
    model = model_by_scope.get(scope_type)
    if model is None:
        raise HTTPException(status_code=422, detail="未知限流范围")
    get_or_404(db, model, _scope_id(scope_key))


def _validate_budget_scope(db: Session, scope_type: str, scope_key: str) -> None:
    if scope_type == "per_request":
        return
    model = models.Project if scope_type == "project_daily" else models.ModelRoute
    get_or_404(db, model, _scope_id(scope_key))


def _scope_id(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="范围标识必须是数字 ID") from exc
    if result < 1:
        raise HTTPException(status_code=422, detail="范围标识必须是正整数 ID")
    return result


def start_of_utc_day() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
