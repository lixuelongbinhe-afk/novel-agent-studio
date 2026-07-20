from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    BudgetPolicyRead,
    BudgetPolicyUpdate,
    BudgetPolicyWrite,
    CapabilityOverrideWrite,
    CapabilityProbeRead,
    CapabilityProbeRequest,
    EffectiveCapabilitiesRead,
    ExecutionPreflightRead,
    ModelDebugRequest,
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
)
from app.services import capabilities, model_control, model_execution
from app.services.control_errors import ModelControlError


router = APIRouter(prefix="/model-center", tags=["model-control"])


@router.get(
    "/models/{model_id}/capabilities", response_model=EffectiveCapabilitiesRead
)
def effective_model_capabilities(
    model_id: int, db: Session = Depends(get_db)
) -> EffectiveCapabilitiesRead:
    return capabilities.effective_capabilities(db, model_id)


@router.put(
    "/models/{model_id}/capabilities/{capability}",
    response_model=EffectiveCapabilitiesRead,
)
def set_capability_override(
    model_id: int,
    capability: str,
    payload: CapabilityOverrideWrite,
    db: Session = Depends(get_db),
) -> EffectiveCapabilitiesRead:
    with db.begin():
        return capabilities.set_manual_override(
            db, model_id, capability, payload.status
        )


@router.delete(
    "/models/{model_id}/capabilities/{capability}",
    response_model=EffectiveCapabilitiesRead,
)
def clear_capability_override(
    model_id: int, capability: str, db: Session = Depends(get_db)
) -> EffectiveCapabilitiesRead:
    with db.begin():
        return capabilities.clear_manual_override(db, model_id, capability)


@router.get(
    "/models/{model_id}/probes", response_model=list[CapabilityProbeRead]
)
def list_capability_probes(
    model_id: int, db: Session = Depends(get_db)
) -> list[CapabilityProbeRead]:
    return capabilities.list_probe_runs(db, model_id)


@router.post(
    "/models/{model_id}/probes",
    response_model=CapabilityProbeRead,
    status_code=status.HTTP_201_CREATED,
)
async def run_capability_probe(
    model_id: int,
    payload: CapabilityProbeRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> CapabilityProbeRead:
    try:
        result = await capabilities.run_capability_probe(
            db, model_id, payload, is_cancelled=request.is_disconnected
        )
        db.commit()
        return result
    except asyncio.CancelledError:
        db.commit()
        raise
    except Exception:
        db.commit()
        raise


@router.get("/models/{model_id}/pricing", response_model=list[ModelPricingRead])
def list_model_pricing(
    model_id: int, db: Session = Depends(get_db)
) -> list[ModelPricingRead]:
    return model_control.list_pricing(db, model_id)


@router.post(
    "/models/{model_id}/pricing",
    response_model=ModelPricingRead,
    status_code=status.HTTP_201_CREATED,
)
def create_model_pricing(
    model_id: int, payload: ModelPricingWrite, db: Session = Depends(get_db)
) -> ModelPricingRead:
    with db.begin():
        return model_control.create_pricing(db, model_id, payload)


@router.delete("/pricing/{pricing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_pricing(
    pricing_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        model_control.delete_pricing(db, pricing_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/routes", response_model=list[ModelRouteRead])
def list_model_routes(
    project_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> list[ModelRouteRead]:
    return model_control.list_routes(db, project_id)


@router.post(
    "/routes", response_model=ModelRouteRead, status_code=status.HTTP_201_CREATED
)
def create_model_route(
    payload: ModelRouteWrite, db: Session = Depends(get_db)
) -> ModelRouteRead:
    with db.begin():
        return model_control.create_route(db, payload)


@router.put("/routes/{route_id}", response_model=ModelRouteRead)
def update_model_route(
    route_id: int, payload: ModelRouteUpdate, db: Session = Depends(get_db)
) -> ModelRouteRead:
    with db.begin():
        return model_control.update_route(db, route_id, payload)


@router.delete("/routes/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_route(
    route_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        model_control.delete_route(db, route_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/rate-limits", response_model=list[RateLimitPolicyRead])
def list_rate_limits(db: Session = Depends(get_db)) -> list[RateLimitPolicyRead]:
    return model_control.list_rate_limits(db)


@router.post(
    "/rate-limits",
    response_model=RateLimitPolicyRead,
    status_code=status.HTTP_201_CREATED,
)
def create_rate_limit(
    payload: RateLimitPolicyWrite, db: Session = Depends(get_db)
) -> RateLimitPolicyRead:
    with db.begin():
        return model_control.create_rate_limit(db, payload)


@router.put("/rate-limits/{policy_id}", response_model=RateLimitPolicyRead)
def update_rate_limit(
    policy_id: int,
    payload: RateLimitPolicyUpdate,
    db: Session = Depends(get_db),
) -> RateLimitPolicyRead:
    with db.begin():
        return model_control.update_rate_limit(db, policy_id, payload)


@router.delete("/rate-limits/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rate_limit(
    policy_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        model_control.delete_rate_limit(db, policy_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/budgets", response_model=list[BudgetPolicyRead])
def list_budgets(db: Session = Depends(get_db)) -> list[BudgetPolicyRead]:
    return model_control.list_budgets(db)


@router.post(
    "/budgets", response_model=BudgetPolicyRead, status_code=status.HTTP_201_CREATED
)
def create_budget(
    payload: BudgetPolicyWrite, db: Session = Depends(get_db)
) -> BudgetPolicyRead:
    with db.begin():
        return model_control.create_budget(db, payload)


@router.put("/budgets/{policy_id}", response_model=BudgetPolicyRead)
def update_budget(
    policy_id: int,
    payload: BudgetPolicyUpdate,
    db: Session = Depends(get_db),
) -> BudgetPolicyRead:
    with db.begin():
        return model_control.update_budget(db, policy_id, payload)


@router.delete("/budgets/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget(
    policy_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        model_control.delete_budget(db, policy_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/health", response_model=list[ProviderHealthRead])
def list_provider_health(db: Session = Depends(get_db)) -> list[ProviderHealthRead]:
    with db.begin():
        return model_control.list_provider_health(db)


@router.post(
    "/health/{provider_id}/reset", response_model=ProviderHealthRead
)
def reset_provider_health(
    provider_id: int, db: Session = Depends(get_db)
) -> ProviderHealthRead:
    with db.begin():
        return model_control.reset_provider_health(db, provider_id)


@router.get("/invocations", response_model=list[ModelInvocationRead])
def list_model_invocations(
    project_id: int | None = Query(default=None, ge=1),
    route_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[ModelInvocationRead]:
    return model_control.list_invocations(
        db, project_id=project_id, route_id=route_id, limit=limit
    )


@router.post("/preflight", response_model=ExecutionPreflightRead)
def preflight_model_execution(
    payload: ModelDebugRequest, db: Session = Depends(get_db)
) -> ExecutionPreflightRead:
    try:
        result = model_execution.preflight_execution(db, payload)
        db.commit()
        return result
    except ModelControlError as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.error.status_code or 409,
            detail=exc.error.model_dump(mode="json"),
        ) from exc
