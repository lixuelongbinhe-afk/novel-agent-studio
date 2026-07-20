from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404
from app.schemas.context import (
    ALL_CLASSIFICATIONS,
    ContentClassificationValue,
    ContextBoundaryRead,
    ContextBuildRead,
    ContextBuildRequest,
    ContextItemRead,
    ContextTargetProviderRead,
    ContextTruncationRead,
)
from app.services import context_memory
from app.services.context_retrieval import (
    CompositeRetriever,
    RetrievalCandidate,
    RetrievalQuery,
    rebuild_fts_index,
)
from app.services.usage_control import estimate_text_tokens


DEFAULT_CLASSIFICATIONS: dict[str, ContentClassificationValue] = {
    "user_task": "internal",
    "upstream": "internal",
    "style_guide": "internal",
    "chapter": "unpublished manuscript",
    "scene": "unpublished manuscript",
    "chapter_summary": "unpublished manuscript",
    "scene_state": "unpublished manuscript",
    "entity": "unpublished manuscript",
    "relation": "unpublished manuscript",
    "timeline": "unpublished manuscript",
    "foreshadow": "unpublished manuscript",
}


@dataclass(frozen=True)
class _ResolvedTarget:
    model_profile_ids: list[int]
    context_window: int | None
    providers: list[ContextTargetProviderRead]
    effective_allowed: list[ContentClassificationValue]
    block_required: bool


def build_context(
    db: Session,
    payload: ContextBuildRequest,
    *,
    retriever: CompositeRetriever | None = None,
) -> ContextBuildRead:
    project = cast(models.Project, get_or_404(db, models.Project, payload.project_id))
    chapter = _validate_chapter(db, payload.chapter_id, project.id)
    scene = _validate_scene(db, payload.scene_id, project.id, chapter)
    agent = _validate_agent(db, payload.agent_id, project.id)
    policy = _resolve_policy(db, payload.policy_id, project.id)
    target = _resolve_target(db, payload, agent)
    policy_allowed = _classification_values(policy.allowed_classifications_json)
    provider_allowed = target.effective_allowed or list(ALL_CLASSIFICATIONS)
    effective_allowed = [
        item for item in policy_allowed if item in provider_allowed
    ]

    model_window = payload.model_context_window
    if target.context_window is not None:
        model_window = (
            min(model_window, target.context_window)
            if model_window is not None
            else target.context_window
        )
    requested_budget = payload.token_budget_override or policy.token_budget
    conflicts: list[str] = []
    blocked = False
    if model_window is not None:
        available_from_model = model_window - payload.reserved_output_tokens
        if available_from_model < 128:
            conflicts.append(
                "模型上下文窗口扣除预留输出后不足 128 Token，不能构建可靠上下文。"
            )
            blocked = True
            token_budget = max(0, available_from_model)
        else:
            token_budget = min(requested_budget, available_from_model)
    else:
        token_budget = requested_budget
    if not target.providers:
        conflicts.append("尚未解析到目标 Provider；本次仅按 ContextPolicy 检查数据边界。")

    rebuild_fts_index(db, project.id)
    agent_type = agent.agent_type if agent is not None else "custom"
    query = RetrievalQuery(
        project_id=project.id,
        chapter_id=chapter.id if chapter is not None else None,
        scene_id=scene.id if scene is not None else None,
        agent_type=agent_type,
        query_text=payload.query,
        workflow_input=payload.workflow_input,
        upstream_outputs=payload.upstream_outputs,
        recent_chapter_count=policy.recent_chapter_count,
        max_results=policy.max_results,
    )
    candidates = (retriever or CompositeRetriever()).retrieve(db, query)
    candidates.extend(_request_candidates(payload))
    candidates = _deduplicate(candidates)
    classifications = _classification_map(db, project.id)
    priorities = _json_int_object(policy.section_priorities_json)
    required_sections = set(_json_string_list(policy.required_sections_json))
    excluded_keys = set(payload.excluded_keys)
    locked_keys = set(payload.locked_keys)

    included_pool: list[ContextItemRead] = []
    excluded: list[ContextItemRead] = []
    boundary_excluded = 0
    required_boundary_excluded = 0
    for candidate in candidates:
        classification = classifications.get(
            (candidate.source_type, candidate.source_id),
            DEFAULT_CLASSIFICATIONS.get(candidate.source_type, "unpublished manuscript"),
        )
        priority = payload.priority_overrides.get(
            candidate.key,
            candidate.pin_priority
            if candidate.pin_priority is not None
            else priorities.get(candidate.section, 50),
        )
        locked = candidate.key in locked_keys
        required = candidate.required or candidate.section in required_sections or locked
        item = _item(
            candidate,
            classification,
            priority=priority,
            required=required,
            locked=locked,
        )
        if candidate.key in excluded_keys:
            item.excluded_reason = "temporary_exclusion"
            excluded.append(item)
            if required:
                conflicts.append(f"锁定或强制上下文被临时排除：{candidate.title}")
                blocked = True
            continue
        if classification not in effective_allowed:
            item.excluded_reason = "provider_data_boundary"
            excluded.append(item)
            boundary_excluded += 1
            if required:
                required_boundary_excluded += 1
                if target.block_required:
                    conflicts.append(
                        f"关键上下文“{candidate.title}”的分类 {classification} "
                        "不在目标 Provider 允许范围内。"
                    )
                    blocked = True
            continue
        if candidate.relevance < policy.min_relevance and not candidate.pinned and not required:
            item.excluded_reason = "below_relevance_threshold"
            excluded.append(item)
            continue
        included_pool.append(item)

    included_pool.sort(key=_rank_key)
    required_items = [item for item in included_pool if item.required]
    optional_items = [item for item in included_pool if not item.required]
    kept_optional = optional_items[: max(0, policy.max_results - len(required_items))]
    for item in optional_items[len(kept_optional) :]:
        item.excluded_reason = "result_limit"
        excluded.append(item)
    selected = [*required_items, *kept_optional]
    truncations: list[ContextTruncationRead] = []
    selected, budget_excluded, budget_truncations, budget_blocked = _fit_budget(
        selected, token_budget
    )
    excluded.extend(budget_excluded)
    truncations.extend(budget_truncations)
    if budget_blocked:
        conflicts.append("强制或锁定上下文超过可用 Token 预算，请提高预算或解除锁定。")
        blocked = True

    selected.sort(key=_rank_key)
    for item in selected:
        item.included = True
    context_text = _render_context(selected)
    included_tokens = estimate_text_tokens(context_text)
    if included_tokens > token_budget and not blocked:
        selected, extra_excluded, extra_truncations, extra_blocked = _fit_budget(
            selected, token_budget, rendered_check=True
        )
        excluded.extend(extra_excluded)
        truncations.extend(extra_truncations)
        context_text = _render_context(selected)
        included_tokens = estimate_text_tokens(context_text)
        blocked = blocked or extra_blocked
        if extra_blocked:
            conflicts.append("渲染后的上下文仍超过 Token 预算。")

    boundary = ContextBoundaryRead(
        policy_allowed=policy_allowed,
        provider_allowed=provider_allowed,
        effective_allowed=effective_allowed,
        excluded_count=boundary_excluded,
        required_excluded_count=required_boundary_excluded,
    )
    build_material = {
        "request": payload.model_dump(mode="json", exclude={"persist_snapshot"}),
        "project_revision": project.revision,
        "policy": context_memory.context_policy_read(policy).model_dump(mode="json"),
        "target_providers": [item.model_dump(mode="json") for item in target.providers],
        "context_text": context_text,
        "included": [item.model_dump(mode="json") for item in selected],
        "excluded": [item.model_dump(mode="json") for item in excluded],
        "truncations": [item.model_dump(mode="json") for item in truncations],
        "boundary": boundary.model_dump(mode="json"),
        "blocked": blocked,
        "conflicts": conflicts,
    }
    build_hash = hashlib.sha256(_dump(build_material).encode("utf-8")).hexdigest()
    result = ContextBuildRead(
        build_hash=build_hash,
        project_id=project.id,
        chapter_id=chapter.id if chapter is not None else None,
        scene_id=scene.id if scene is not None else None,
        agent_id=agent.id if agent is not None else None,
        model_profile_id=(
            payload.model_profile_id
            or (agent.model_profile_id if agent is not None else None)
        ),
        policy_id=policy.id,
        target_providers=target.providers,
        token_budget=token_budget,
        reserved_output_tokens=payload.reserved_output_tokens,
        included_tokens=included_tokens,
        context_text=context_text,
        included=selected,
        excluded=sorted(excluded, key=_rank_key),
        truncations=truncations,
        boundary=boundary,
        blocked=blocked,
        conflicts=conflicts,
    )
    if payload.persist_snapshot:
        row = models.ContextBuild(
            project_id=result.project_id,
            chapter_id=result.chapter_id,
            scene_id=result.scene_id,
            agent_id=result.agent_id,
            workflow_run_id=payload.workflow_run_id,
            model_profile_id=result.model_profile_id,
            policy_id=result.policy_id,
            provider_ids_json=_dump(
                [item.provider_account_id for item in result.target_providers]
            ),
            request_json=_dump(payload.model_dump(mode="json")),
            result_json=_dump(result.model_dump(mode="json")),
            context_text=result.context_text,
            build_hash=result.build_hash,
        )
        db.add(row)
        db.flush()
        result.id = row.id
        result.created_at = row.created_at
    return result


def read_context_build(db: Session, build_id: int) -> ContextBuildRead:
    row = db.get(models.ContextBuild, build_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ContextBuild not found")
    value = _json_object(row.result_json)
    result = ContextBuildRead.model_validate(value)
    result.id = row.id
    result.created_at = row.created_at
    if result.build_hash != row.build_hash or result.context_text != row.context_text:
        raise HTTPException(status_code=500, detail="上下文快照完整性校验失败")
    return result


def list_context_builds(
    db: Session, project_id: int, *, limit: int = 100
) -> list[ContextBuildRead]:
    get_or_404(db, models.Project, project_id)
    rows = db.scalars(
        select(models.ContextBuild)
        .where(models.ContextBuild.project_id == project_id)
        .order_by(models.ContextBuild.id.desc())
        .limit(limit)
    ).all()
    return [read_context_build(db, row.id) for row in rows]


def _resolve_policy(
    db: Session, policy_id: int | None, project_id: int
) -> models.ContextPolicy:
    if policy_id is None:
        row = context_memory.ensure_default_context_policy(db, project_id)
    else:
        row = cast(models.ContextPolicy, get_or_404(db, models.ContextPolicy, policy_id))
    if row.project_id != project_id:
        raise HTTPException(status_code=422, detail="ContextPolicy 不属于当前项目")
    if not row.enabled:
        raise HTTPException(status_code=409, detail="ContextPolicy 已停用")
    return row


def _resolve_target(
    db: Session,
    payload: ContextBuildRequest,
    agent: models.AgentDefinition | None,
) -> _ResolvedTarget:
    model_ids: list[int] = []
    if payload.model_profile_id is not None:
        model_ids = [payload.model_profile_id]
    elif agent is not None and agent.model_profile_id is not None:
        model_ids = [agent.model_profile_id]
    elif agent is not None and agent.route_id is not None:
        model_ids = list(
            db.scalars(
                select(models.ModelRouteEntry.model_profile_id)
                .join(models.ModelProfile, models.ModelProfile.id == models.ModelRouteEntry.model_profile_id)
                .where(
                    models.ModelRouteEntry.route_id == agent.route_id,
                    models.ModelRouteEntry.deleted_at.is_(None),
                    models.ModelRouteEntry.enabled.is_(True),
                    models.ModelProfile.deleted_at.is_(None),
                    models.ModelProfile.enabled.is_(True),
                )
                .order_by(models.ModelRouteEntry.position, models.ModelRouteEntry.id)
            ).all()
        )
    profiles: list[models.ModelProfile] = []
    for model_id in dict.fromkeys(model_ids):
        profile = cast(models.ModelProfile, get_or_404(db, models.ModelProfile, model_id))
        if not profile.enabled:
            raise HTTPException(status_code=409, detail=f"ModelProfile #{model_id} 已停用")
        profiles.append(profile)
    by_provider: dict[int, list[int]] = {}
    for profile in profiles:
        by_provider.setdefault(profile.provider_account_id, []).append(profile.id)
    targets: list[ContextTargetProviderRead] = []
    allowed_sets: list[set[str]] = []
    block_required = False
    for provider_id, profile_ids in sorted(by_provider.items()):
        provider = cast(
            models.ProviderAccount, get_or_404(db, models.ProviderAccount, provider_id)
        )
        if not provider.enabled:
            raise HTTPException(status_code=409, detail=f"Provider {provider.name} 已停用")
        stored = db.scalar(
            select(models.ProviderDataPolicy).where(
                models.ProviderDataPolicy.provider_account_id == provider.id,
                models.ProviderDataPolicy.deleted_at.is_(None),
                models.ProviderDataPolicy.enabled.is_(True),
            )
        )
        if stored is not None:
            allowed = _classification_values(stored.allowed_classifications_json)
            policy_source = "stored"
            block_required = block_required or stored.block_on_required_exclusion
        else:
            allowed = cast(
                list[ContentClassificationValue],
                context_memory.default_provider_classifications(provider),
            )
            policy_source = (
                "local_default" if context_memory.is_local_provider(provider) else "remote_default"
            )
            block_required = True
        allowed_sets.append(set(allowed))
        targets.append(
            ContextTargetProviderRead(
                provider_account_id=provider.id,
                provider_name=provider.name,
                provider_type=provider.provider_type,
                model_profile_ids=profile_ids,
                allowed_classifications=allowed,
                policy_source=cast(Any, policy_source),
            )
        )
    if allowed_sets:
        intersection = set.intersection(*allowed_sets)
        effective = [item for item in ALL_CLASSIFICATIONS if item in intersection]
    else:
        effective = []
    context_window = min((item.context_window for item in profiles), default=None)
    return _ResolvedTarget(
        model_profile_ids=[item.id for item in profiles],
        context_window=context_window,
        providers=targets,
        effective_allowed=effective,
        block_required=block_required,
    )


def _request_candidates(payload: ContextBuildRequest) -> list[RetrievalCandidate]:
    result: list[RetrievalCandidate] = []
    task_parts: list[str] = []
    if payload.query.strip():
        task_parts.append(payload.query.strip())
    if payload.workflow_input:
        task_parts.append("工作流输入：\n" + _pretty_json(payload.workflow_input))
    if task_parts:
        result.append(
            RetrievalCandidate(
                source_type="user_task",
                source_id=payload.project_id,
                section="user_task",
                title="用户任务",
                content="\n\n".join(task_parts),
                relevance=1.0,
                reasons=["本次 ContextBuilder 的显式任务与工作流输入"],
                required=True,
            )
        )
    if payload.upstream_outputs:
        result.append(
            RetrievalCandidate(
                source_type="upstream",
                source_id=payload.workflow_run_id or payload.project_id,
                section="upstream",
                title="工作流上游输出",
                content=_pretty_json(payload.upstream_outputs),
                relevance=0.96,
                reasons=["当前工作流的真实上游节点输出"],
                required=False,
            )
        )
    return result


def _classification_map(
    db: Session, project_id: int
) -> dict[tuple[str, int], ContentClassificationValue]:
    rows = db.scalars(
        select(models.ContentClassification).where(
            models.ContentClassification.project_id == project_id,
            models.ContentClassification.deleted_at.is_(None),
        )
    ).all()
    result: dict[tuple[str, int], ContentClassificationValue] = {}
    for row in rows:
        if row.classification in ALL_CLASSIFICATIONS:
            result[(row.source_type, row.source_id)] = row.classification
    return result


def _deduplicate(candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
    result: dict[str, RetrievalCandidate] = {}
    for candidate in candidates:
        identity = f"{candidate.source_type}:{candidate.source_id}"
        existing = result.get(identity)
        if existing is None:
            result[identity] = candidate
            continue
        existing.relevance = max(existing.relevance, candidate.relevance)
        existing.reasons = list(dict.fromkeys([*existing.reasons, *candidate.reasons]))
        existing.metadata.update(candidate.metadata)
        existing.required = existing.required or candidate.required
        if candidate.pinned:
            existing.pinned = True
            existing.pin_priority = candidate.pin_priority
            existing.content = candidate.content
            existing.title = candidate.title
    return list(result.values())


def _item(
    candidate: RetrievalCandidate,
    classification: ContentClassificationValue,
    *,
    priority: int,
    required: bool,
    locked: bool,
) -> ContextItemRead:
    tokens = estimate_text_tokens(_block_text(candidate.title, candidate.content))
    return ContextItemRead(
        key=candidate.key,
        source_type=candidate.source_type,
        source_id=candidate.source_id,
        section=candidate.section,
        title=candidate.title,
        content=candidate.content,
        relevance=round(max(0.0, min(1.0, candidate.relevance)), 4),
        reasons=candidate.reasons,
        token_estimate=tokens,
        original_token_estimate=tokens,
        classification=classification,
        pinned=candidate.pinned,
        priority=priority,
        required=required,
        locked=locked,
        included=False,
        metadata=candidate.metadata,
    )


def _fit_budget(
    items: list[ContextItemRead],
    budget: int,
    *,
    rendered_check: bool = False,
) -> tuple[
    list[ContextItemRead],
    list[ContextItemRead],
    list[ContextTruncationRead],
    bool,
]:
    selected = list(items)
    excluded: list[ContextItemRead] = []
    truncations: list[ContextTruncationRead] = []
    required_tokens = estimate_text_tokens(
        _render_context([item for item in selected if item.required])
    )
    if required_tokens > budget:
        return selected, excluded, truncations, True

    def total() -> int:
        return estimate_text_tokens(_render_context(selected))

    while selected and total() > budget:
        optional = sorted(
            (item for item in selected if not item.required),
            key=lambda item: (item.priority, item.relevance, item.pinned, item.key),
        )
        if not optional:
            return selected, excluded, truncations, True
        item = optional[0]
        overage = total() - budget
        minimum = estimate_text_tokens(_block_text(item.title, "…"))
        if item.token_estimate - minimum > overage and item.token_estimate > 48:
            target = max(minimum, item.token_estimate - overage - 2)
            original = item.token_estimate
            item.content = _truncate_content(item.title, item.content, target)
            item.token_estimate = estimate_text_tokens(_block_text(item.title, item.content))
            item.truncated = True
            truncations.append(
                ContextTruncationRead(
                    key=item.key,
                    original_tokens=original,
                    final_tokens=item.token_estimate,
                    strategy="truncate",
                    reason=(
                        "渲染后预算校正" if rendered_check else "Token 预算不足，截断非强制低优先级上下文"
                    ),
                )
            )
            if total() <= budget:
                break
        selected.remove(item)
        item.included = False
        item.excluded_reason = "budget_low_relevance"
        excluded.append(item)
        if item.section == "neighbor_summaries":
            truncations.append(
                ContextTruncationRead(
                    key=item.key,
                    original_tokens=item.original_token_estimate,
                    final_tokens=0,
                    strategy="omit_neighbor",
                    reason="Token 预算不足，减少邻近章节数量",
                )
            )
    return selected, excluded, truncations, False


def _truncate_content(title: str, content: str, target_tokens: int) -> str:
    suffix = "\n[已按 Token 预算截断]"
    low = 0
    high = len(content)
    best = suffix
    while low <= high:
        middle = (low + high) // 2
        candidate = content[:middle].rstrip() + suffix
        tokens = estimate_text_tokens(_block_text(title, candidate))
        if tokens <= target_tokens:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _rank_key(item: ContextItemRead) -> tuple[int, int, float, str]:
    return (-int(item.required), -item.priority, -item.relevance, item.key)


def _render_context(items: list[ContextItemRead]) -> str:
    return "\n\n".join(_block_text(item.title, item.content) for item in items)


def _block_text(title: str, content: str) -> str:
    return f"## {title}\n{content.strip()}"


def _validate_chapter(
    db: Session, chapter_id: int | None, project_id: int
) -> models.Chapter | None:
    if chapter_id is None:
        return None
    chapter = cast(models.Chapter, get_or_404(db, models.Chapter, chapter_id))
    owner = db.scalar(
        select(models.Volume.project_id).where(models.Volume.id == chapter.volume_id)
    )
    if owner != project_id:
        raise HTTPException(status_code=422, detail="Chapter 不属于当前项目")
    return chapter


def _validate_scene(
    db: Session,
    scene_id: int | None,
    project_id: int,
    chapter: models.Chapter | None,
) -> models.Scene | None:
    if scene_id is None:
        return None
    scene = cast(models.Scene, get_or_404(db, models.Scene, scene_id))
    owner_chapter = cast(models.Chapter, get_or_404(db, models.Chapter, scene.chapter_id))
    owner = db.scalar(
        select(models.Volume.project_id).where(models.Volume.id == owner_chapter.volume_id)
    )
    if owner != project_id:
        raise HTTPException(status_code=422, detail="Scene 不属于当前项目")
    if chapter is not None and scene.chapter_id != chapter.id:
        raise HTTPException(status_code=422, detail="Scene 不属于所选 Chapter")
    return scene


def _validate_agent(
    db: Session, agent_id: int | None, project_id: int
) -> models.AgentDefinition | None:
    if agent_id is None:
        return None
    agent = cast(models.AgentDefinition, get_or_404(db, models.AgentDefinition, agent_id))
    if agent.project_id != project_id:
        raise HTTPException(status_code=422, detail="Agent 不属于当前项目")
    if not agent.enabled:
        raise HTTPException(status_code=409, detail="Agent 已停用")
    return agent


def _classification_values(value: str) -> list[ContentClassificationValue]:
    return [
        item
        for item in _json_string_list(value)
        if item in ALL_CLASSIFICATIONS
    ]


def _json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _json_string_list(value: str) -> list[str]:
    result = _json_value(value)
    return [str(item) for item in result] if isinstance(result, list) else []


def _json_object(value: str) -> dict[str, Any]:
    result = _json_value(value)
    return result if isinstance(result, dict) else {}


def _json_int_object(value: str) -> dict[str, int]:
    result = _json_object(value)
    return {
        str(key): int(item)
        for key, item in result.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }


def _pretty_json(value: dict[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
