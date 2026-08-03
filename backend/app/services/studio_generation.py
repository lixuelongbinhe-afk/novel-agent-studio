from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.schemas import ModelDebugRequest, NormalizedContentPart, NormalizedMessage
from app.schemas.context import ContextBuildRequest
from app.schemas.studio import GenerateResult, GenerateRequest
from app.services import context_builder, generation_jobs, model_execution
from app.services.chapter_tree import (
    _approved_volume_titles,
    _chapter_generation_ranges,
    _chapter_plan_excerpt,
    _chapter_plan_validation,
    _format_number_ranges,
    _missing_chapter_numbers,
)
from app.services.credential_store import has_provider_secret
from app.services.errors import (
    BudgetPausedError,
    ConflictError,
    DomainError,
    InvalidInputError,
    UnavailableError,
    UpstreamFailedError,
)
from app.services.usage_control import estimate_text_tokens


STAGE_LABELS = {
    "idea": "创意简报",
    "world": "世界观与风格",
    "characters": "人物与关系",
    "plot": "剧情、时间线与伏笔",
    "volumes": "分卷大纲",
    "chapters": "章节与场景大纲",
    "drafting": "正文创作",
    "review": "全文审阅",
    "complete": "完成",
    "continuation_import": "导入与解析",
    "continuation_analysis": "资料审核",
    "continuation_outline": "大纲补建",
    "continuation_plan": "续写规划",
}


def _studio_state(db: Session, project_id: int) -> models.StudioProjectState:
    from app.services import studio

    return studio._state(db, project_id)


def _studio_project(db: Session, project_id: int) -> models.Project:
    from app.services import studio

    return studio._project(db, project_id)


def _studio_json(value: str) -> dict[str, JsonValue]:
    from app.services import studio

    return cast(dict[str, JsonValue], studio._json_object(value))


def _stage_order(state: models.StudioProjectState) -> list[str]:
    from app.services import studio

    return studio._stage_order(state)


def _phase_complete(db: Session, project_id: int, phase: str) -> bool:
    from app.services import studio

    return studio._phase_complete(db, project_id, phase)


def _supersede_series(db: Session, project_id: int, series_key: str) -> None:
    from app.services import studio

    studio._supersede_series(db, project_id, series_key)


def _new_artifact(
    project_id: int,
    kind: str,
    title: str,
    content: str,
    metadata: Mapping[str, object],
    position: int,
) -> models.CreativeArtifact:
    from app.services import studio

    return studio._new_artifact(project_id, kind, title, content, metadata, position)


def _mark_conflicts(artifact: models.CreativeArtifact) -> None:
    from app.services import studio

    studio._mark_conflicts(artifact)


def _artifact_record(artifact: models.CreativeArtifact) -> dict[str, JsonValue]:
    from app.services import studio

    return cast(dict[str, JsonValue], studio._artifact_record(artifact))


def _price_score(db: Session, profile_id: int) -> float:
    from app.services import studio

    return studio._price_score(db, profile_id)


def _latency(db: Session, provider_id: int) -> int:
    from app.services import studio

    return studio._latency(db, provider_id)


@dataclass(frozen=True)
class ValidatedGenerationRequest:
    project_id: int
    phase: str
    payload: GenerateRequest
    project: models.Project
    state: models.StudioProjectState
    phase_agents: tuple[tuple[str, str], ...]
    profile: models.ModelProfile | None
    reason: str
    chapter_ranges: tuple[tuple[int, int], ...]
    phase_max_tokens: int

    @property
    def total_calls(self) -> int:
        return len(self.phase_agents) * len(self.chapter_ranges)


@dataclass(frozen=True)
class BuiltGenerationContext:
    text: str
    metadata: dict[str, JsonValue]


@dataclass
class GenerationProgress:
    completed_calls: int = 0


@dataclass(frozen=True)
class PhaseOutputs:
    values: tuple[str, ...]
    context: BuiltGenerationContext


def _validate_generation_request(
    db: Session,
    project_id: int,
    phase: str,
    payload: GenerateRequest,
) -> ValidatedGenerationRequest:
    from app.services import studio

    if phase not in studio.PHASE_AGENTS:
        raise InvalidInputError("不支持的创作阶段")
    project = studio._project(db, project_id)
    state = studio._state(db, project_id)
    studio._require_generation_prerequisites(db, project_id, phase, payload)
    phase_agents = tuple(studio.PHASE_AGENTS[phase])
    if payload.agent_name is not None:
        phase_agents = tuple(item for item in phase_agents if item[0] == payload.agent_name)
        if not phase_agents:
            raise InvalidInputError("该阶段不存在指定的 Agent")
    if state.budget_paused:
        raise BudgetPausedError("项目预算已暂停，请先在费用面板确认继续")
    profile, reason = studio._select_model(db, state, payload.use_demo_model)
    requested_chapters = max(
        1,
        min(int(studio._json_object(state.config_json).get("chapter_count") or 12), 10_000),
    )
    chapter_ranges = tuple(
        _chapter_generation_ranges(requested_chapters) if phase == "chapters" else [(1, 1)]
    )
    return ValidatedGenerationRequest(
        project_id=project_id,
        phase=phase,
        payload=payload,
        project=project,
        state=state,
        phase_agents=phase_agents,
        profile=profile,
        reason=reason,
        chapter_ranges=chapter_ranges,
        phase_max_tokens=_phase_output_tokens(phase),
    )


async def _acquire_lease(
    db: Session, request: ValidatedGenerationRequest
) -> generation_jobs.GenerationLease:
    try:
        return await generation_jobs.acquire_async(
            db,
            project_id=request.project_id,
            phase=request.phase,
            chapter_id=request.payload.chapter_id,
            mode=request.payload.mode,
            idempotency_key=request.payload.idempotency_key,
            label=(
                f"{STAGE_LABELS.get(request.phase, request.phase)} · "
                f"{len(request.phase_agents)} 个 Agent"
            ),
            model_name=(
                request.profile.display_name if request.profile is not None else "内置演示模型"
            ),
            model_reason=request.reason,
        )
    except generation_jobs.GenerationLeaseConflict as exc:
        raise UnavailableError("生成任务租约暂时不可用，请稍后重试") from exc


def _generation_job_artifacts(
    db: Session, job: models.GenerationJob
) -> list[models.CreativeArtifact]:
    from app.services import studio

    candidates = db.scalars(
        select(models.CreativeArtifact)
        .where(
            models.CreativeArtifact.project_id == job.project_id,
            models.CreativeArtifact.deleted_at.is_(None),
        )
        .order_by(models.CreativeArtifact.position, models.CreativeArtifact.id)
    ).all()
    matches = [
        item
        for item in candidates
        if studio._json_object(item.metadata_json).get("generation_idempotency_key")
        == job.idempotency_key
    ]
    if matches:
        return matches
    fallback = db.get(models.CreativeArtifact, job.result_artifact_id or 0)
    return [fallback] if fallback is not None else []


def _replayed_result(db: Session, job: models.GenerationJob) -> GenerateResult:
    from app.services import studio

    artifacts = _generation_job_artifacts(db, job)
    first = artifacts[0] if artifacts else None
    return GenerateResult.model_validate(
        {
            "job": studio._record(job),
            "artifact": studio._artifact_record(first) if first is not None else None,
            "artifacts": [studio._artifact_record(item) for item in artifacts],
            "idempotent_replay": True,
        }
    )


async def _build_generation_context(
    db: Session,
    request: ValidatedGenerationRequest,
    job: models.GenerationJob,
) -> BuiltGenerationContext:
    from app.services import studio

    context, raw_metadata = studio._generation_context(
        db,
        request.project_id,
        request.payload.chapter_id,
        profile=request.profile,
        use_demo=request.payload.use_demo_model,
        max_tokens=request.phase_max_tokens,
        query=(
            f"{STAGE_LABELS.get(request.phase, request.phase)}；"
            f"{request.payload.instruction or '按已审核资料执行'}"
        ),
    )
    if request.phase in {"continuation_analysis", "continuation_outline"}:
        mapped_context, mapped_metadata = await studio._continuation_source_context(
            db,
            request.project_id,
            request.phase,
            request.profile,
            use_demo=request.payload.use_demo_model,
            max_tokens=request.phase_max_tokens,
        )
        context = studio._fit_text_to_token_budget(
            f"{context}\n\n{mapped_context}",
            studio._studio_input_budget(
                request.profile,
                request.payload.use_demo_model,
                request.phase_max_tokens,
            ),
        )
        raw_metadata.update(mapped_metadata)
    job.model_reason = f"{request.reason} {studio._context_reason(raw_metadata)}"
    db.commit()
    return BuiltGenerationContext(
        text=context,
        metadata=cast(dict[str, JsonValue], raw_metadata),
    )


async def _run_phase_agents(
    db: Session,
    request: ValidatedGenerationRequest,
    job: models.GenerationJob,
    context: BuiltGenerationContext,
    progress: GenerationProgress,
) -> PhaseOutputs:
    from app.services import studio

    outputs: list[str] = []
    requested_chapters = max(
        1,
        min(
            int(studio._json_object(request.state.config_json).get("chapter_count") or 12),
            10_000,
        ),
    )
    for agent_name, responsibility in request.phase_agents:
        agent_parts: list[str] = []
        for range_start, range_end in request.chapter_ranges:
            batch_payload = request.payload
            collaborator_outputs = outputs
            if request.phase == "chapters":
                batch_requirement = (
                    f"本次只规划第 {range_start} 至第 {range_end} 章，共 "
                    f"{range_end - range_start + 1} 章。必须逐章输出二级标题“## 第N章 标题”，"
                    "不得省略、合并或输出范围外章节。"
                )
                instruction = "\n".join(
                    item
                    for item in [request.payload.instruction.strip(), batch_requirement]
                    if item
                )
                batch_payload = request.payload.model_copy(update={"instruction": instruction})
                collaborator_outputs = [
                    _chapter_plan_excerpt(item, range_start, range_end) for item in outputs
                ]
            prompt = studio._phase_prompt(
                request.project,
                request.phase,
                agent_name,
                responsibility,
                context.text,
                batch_payload,
                collaborator_outputs,
            )
            response = await studio._model_call(
                db,
                request.project_id,
                prompt,
                request.profile,
                use_demo=request.payload.use_demo_model,
                max_tokens=request.phase_max_tokens,
            )
            if response.error is not None:
                raise RuntimeError(f"{response.error.code}: {response.error.message}")
            response_text = response.text.strip()
            studio._record_response_cost(request.state, response)
            if request.phase == "chapters" and agent_name == "章节规划师":
                missing = _missing_chapter_numbers(response_text, range_start, range_end)
                if missing:
                    repair_prompt = (
                        f"{prompt}\n\n上一次输出缺少以下章节：{_format_number_ranges(missing)}。"
                        "请只补充这些缺失章节，每章必须使用二级标题“## 第N章 标题”，"
                        "不要重写已经生成的章节。"
                    )
                    repair_response = await studio._model_call(
                        db,
                        request.project_id,
                        repair_prompt,
                        request.profile,
                        use_demo=request.payload.use_demo_model,
                        max_tokens=request.phase_max_tokens,
                    )
                    if repair_response.error is not None:
                        raise RuntimeError(
                            f"{repair_response.error.code}: {repair_response.error.message}"
                        )
                    studio._record_response_cost(request.state, repair_response)
                    response_text = (response_text + "\n\n" + repair_response.text.strip()).strip()
            agent_parts.append(response_text)
            progress.completed_calls += 1
            job.progress = min(
                90,
                int((progress.completed_calls / request.total_calls) * 85) + 5,
            )
            db.commit()
        agent_output = f"## {agent_name}\n\n" + "\n\n".join(agent_parts)
        if request.phase == "chapters" and agent_name == "章节规划师":
            validation = _chapter_plan_validation(
                agent_output,
                requested_chapters,
                _approved_volume_titles(db, request.project_id),
            )
            agent_output = str(validation["preview_markdown"])
        outputs.append(agent_output)
    return PhaseOutputs(values=tuple(outputs), context=context)


async def _finalize_generation(
    db: Session,
    request: ValidatedGenerationRequest,
    job: models.GenerationJob,
    phase_outputs: PhaseOutputs,
) -> GenerateResult:
    from app.services import studio

    outputs = list(phase_outputs.values)
    requested_chapters = max(
        1,
        min(
            int(studio._json_object(request.state.config_json).get("chapter_count") or 12),
            10_000,
        ),
    )
    metadata: dict[str, JsonValue] = {
        "agents": [name for name, _ in request.phase_agents],
        "model": job.model_name,
        "model_reason": request.reason,
        "chapter_id": request.payload.chapter_id,
        "mode": request.payload.mode,
        "context": phase_outputs.context.metadata,
        "generation_idempotency_key": request.payload.idempotency_key,
    }
    artifact_kind = request.phase
    if request.payload.mode not in {"new", "continue"}:
        artifact_kind = "revision_proposal"
        metadata["revision_mode"] = request.payload.mode
        metadata["selected_text"] = request.payload.selected_text
    artifacts: list[models.CreativeArtifact] = []
    planning_phases = {
        "world",
        "characters",
        "plot",
        "volumes",
        "chapters",
        "continuation_analysis",
        "continuation_outline",
        "continuation_plan",
    }
    if request.phase in planning_phases:
        for agent_index, ((agent_name, _), output) in enumerate(
            zip(request.phase_agents, outputs, strict=True)
        ):
            item_metadata = dict(metadata)
            item_metadata.update(
                {
                    "agent_name": agent_name,
                    "agent_index": agent_index,
                    "required_count": len(studio.PHASE_AGENTS[request.phase]),
                    "series_key": f"{request.phase}:{agent_name}",
                }
            )
            if request.phase == "chapters" and agent_name == "章节规划师":
                validation = _chapter_plan_validation(
                    output,
                    requested_chapters,
                    _approved_volume_titles(db, request.project_id),
                )
                item_metadata["chapter_plan_validation"] = cast(
                    JsonValue,
                    {
                        key: value
                        for key, value in validation.items()
                        if key not in {"volumes", "preview_markdown"}
                    },
                )
                item_metadata["normalized_preview"] = True
            studio._supersede_series(db, request.project_id, str(item_metadata["series_key"]))
            artifacts.append(
                studio._new_artifact(
                    request.project_id,
                    artifact_kind,
                    agent_name,
                    output,
                    item_metadata,
                    agent_index,
                )
            )
    elif request.phase == "drafting" and request.state.review_granularity == "scene":
        chapter = db.get(models.Chapter, int(request.payload.chapter_id or 0))
        scenes = db.scalars(
            select(models.Scene)
            .where(
                models.Scene.chapter_id == int(request.payload.chapter_id or 0),
                models.Scene.deleted_at.is_(None),
            )
            .order_by(models.Scene.position)
        ).all()
        if chapter is None or not scenes:
            raise ConflictError("场景级审核需要该章节先建立场景大纲")
        combined = "\n\n".join(outputs)
        previous_scene = ""
        for scene_index, scene in enumerate(scenes):
            scene_metadata = dict(metadata)
            scene_metadata.update(
                {
                    "scene_id": scene.id,
                    "scene_index": scene_index,
                    "series_key": f"scene:{scene.id}",
                }
            )
            scene_prompt = (
                f"请只写小说《{request.project.title}》中“{chapter.title}”的场景正文。\n"
                f"场景：{scene.title}\n场景要求：{scene.synopsis or '按章节大纲完成本场景。'}\n"
                f"前一场景结尾：{previous_scene[-1200:] or '这是本章首场。'}\n\n"
                f"项目上下文：\n{phase_outputs.context.text}\n\n同章编辑建议：\n{combined}\n\n"
                "输出可直接进入小说的正文，不要输出分析、标题或创作说明。"
            )
            scene_response = await studio._model_call(
                db,
                request.project_id,
                scene_prompt,
                request.profile,
                use_demo=request.payload.use_demo_model,
                max_tokens=3600,
            )
            if scene_response.error is not None:
                raise RuntimeError(f"{scene_response.error.code}: {scene_response.error.message}")
            studio._record_response_cost(request.state, scene_response)
            content = scene_response.text.strip()
            previous_scene = content
            artifacts.append(
                studio._new_artifact(
                    request.project_id,
                    "scene_draft",
                    scene.title,
                    content,
                    scene_metadata,
                    scene_index,
                )
            )
        for artifact in artifacts:
            studio._supersede_series(
                db,
                request.project_id,
                str(studio._json_object(artifact.metadata_json)["series_key"]),
            )
    else:
        metadata["series_key"] = (
            f"{artifact_kind}:{request.payload.chapter_id or 0}:{request.payload.mode}"
        )
        studio._supersede_series(db, request.project_id, str(metadata["series_key"]))
        content = outputs[-1] if request.phase == "drafting" else "\n\n".join(outputs)
        artifacts.append(
            studio._new_artifact(
                request.project_id,
                artifact_kind,
                studio._artifact_title(request.phase, request.payload),
                content,
                metadata,
                0,
            )
        )
    for artifact in artifacts:
        studio._mark_conflicts(artifact)
        db.add(artifact)
    if request.state.entry_mode == "continuation" and any(
        studio._json_object(artifact.metadata_json).get("requires_author_decision")
        for artifact in artifacts
    ):
        state_config = studio._json_object(request.state.config_json)
        state_config["conflict_paused"] = True
        request.state.config_json = studio._dump(state_config)
        request.state.revision += 1
    db.flush()
    generation_jobs.complete(db, job, result_artifact_id=artifacts[0].id)
    studio._apply_budget_after_task(request.state)
    db.commit()
    return GenerateResult.model_validate(
        {
            "job": studio._record(job),
            "artifact": studio._artifact_record(artifacts[0]),
            "artifacts": [studio._artifact_record(item) for item in artifacts],
            "idempotent_replay": False,
        }
    )


def _generation_failure_message(reason: str, completed: int, total: int) -> str:
    return generation_jobs.failure_message(reason, completed, total)


def _mark_job_failed(
    db: Session,
    job: models.GenerationJob,
    reason: str,
    progress: GenerationProgress,
    total_calls: int,
    *,
    cancelled: bool = False,
) -> None:
    generation_jobs.fail(
        db,
        job.id,
        _generation_failure_message(reason, progress.completed_calls, total_calls),
        cancelled=cancelled,
    )


async def generate(
    db: Session, project_id: int, phase: str, payload: GenerateRequest
) -> GenerateResult:
    request = _validate_generation_request(db, project_id, phase, payload)
    lease = await _acquire_lease(db, request)
    if lease.replayed:
        return _replayed_result(db, lease.job)
    progress = GenerationProgress()
    try:
        context = await _build_generation_context(db, request, lease.job)
        outputs = await _run_phase_agents(db, request, lease.job, context, progress)
        return await _finalize_generation(db, request, lease.job, outputs)
    except asyncio.CancelledError:
        _mark_job_failed(
            db,
            lease.job,
            "生成任务已取消",
            progress,
            request.total_calls,
            cancelled=True,
        )
        raise
    except DomainError as exc:
        _mark_job_failed(
            db,
            lease.job,
            str(exc.detail),
            progress,
            request.total_calls,
        )
        raise
    except Exception as exc:
        message = _generation_failure_message(
            str(exc), progress.completed_calls, request.total_calls
        )
        generation_jobs.fail(db, lease.job.id, message)
        raise UpstreamFailedError(message) from exc


def _require_generation_prerequisites(
    db: Session,
    project_id: int,
    phase: str,
    payload: GenerateRequest,
) -> None:
    state = _studio_state(db, project_id)
    order = _stage_order(state)
    if phase not in order or state.stage not in order:
        raise ConflictError("当前项目模式不支持该创作阶段")
    config = _studio_json(state.config_json)
    if state.entry_mode == "continuation" and config.get("conflict_paused"):
        raise ConflictError("发现重大连续性冲突，必须由作者确认处理后才能继续")
    phase_index = order.index(phase)
    current_index = order.index(state.stage)
    if phase_index > current_index and not (state.stage == "idea" and phase == "world"):
        raise ConflictError(f"请先完成并批准“{STAGE_LABELS[state.stage]}”阶段")
    if phase == "drafting":
        if not payload.chapter_id:
            raise InvalidInputError("正文生成必须选择章节")
        if state.entry_mode == "continuation" and config.get("continuation_start") == "choose":
            raise ConflictError("请先选择接着写当前章或从下一章开始")
        planning = (
            ["continuation_analysis", "continuation_outline", "continuation_plan"]
            if state.entry_mode == "continuation"
            else ["world", "characters", "plot", "volumes", "chapters"]
        )
        if state.entry_mode in {"creative", "continuation"} and not all(
            _phase_complete(db, project_id, item) for item in planning
        ):
            raise ConflictError("所有规划成果分别批准后才能开始正文")
        pending_planning = int(
            db.scalar(
                select(func.count(models.CreativeArtifact.id)).where(
                    models.CreativeArtifact.project_id == project_id,
                    models.CreativeArtifact.kind.in_(planning),
                    models.CreativeArtifact.status.in_(["pending", "changes_requested"]),
                )
            )
            or 0
        )
        if pending_planning:
            raise ConflictError("仍有规划成果待审核，不能开始正文")
    if phase == "review":
        volume_ids = select(models.Volume.id).where(models.Volume.project_id == project_id)
        empty_chapters = int(
            db.scalar(
                select(func.count(models.Chapter.id)).where(
                    models.Chapter.volume_id.in_(volume_ids),
                    models.Chapter.word_count == 0,
                )
            )
            or 0
        )
        if empty_chapters:
            raise ConflictError("仍有章节未完成，不能开始全文审阅")


def _maybe_finish_drafting(db: Session, project_id: int) -> None:
    volume_ids = select(models.Volume.id).where(models.Volume.project_id == project_id)
    empty = int(
        db.scalar(
            select(func.count(models.Chapter.id)).where(
                models.Chapter.volume_id.in_(volume_ids),
                models.Chapter.word_count == 0,
                models.Chapter.deleted_at.is_(None),
            )
        )
        or 0
    )
    if empty == 0:
        state = _studio_state(db, project_id)
        state.stage = "review"
        state.revision += 1


async def extract_style_reference(
    db: Session,
    project_id: int,
    text: str,
    filename: str,
    use_demo_model: bool,
) -> dict[str, Any]:
    project = _studio_project(db, project_id)
    state = _studio_state(db, project_id)
    if state.budget_paused:
        raise BudgetPausedError("项目预算已暂停，请先在费用面板确认继续")
    profile, reason = _select_model(db, state, use_demo_model)
    input_budget = max(512, _studio_input_budget(profile, use_demo_model, 2200) - 600)
    chunks = _chunk_text_by_tokens(text, input_budget)
    partials: list[str] = []
    for index, chunk in enumerate(chunks):
        prompt = (
            "分析以下作者合法提供的参考文本分片，只提取可复用的抽象文风特征，"
            "不续写、不模仿具体句子。输出叙事视角、句式长度、节奏、描写密度、"
            "对白特点、常用意象和应避免事项。\n\n"
            f"项目：{project.title}\n文件：{filename}\n"
            f"分片：{index + 1}/{len(chunks)}\n\n参考文本：\n{chunk}"
        )
        partial = await _model_call(
            db,
            project_id,
            prompt,
            profile,
            use_demo=use_demo_model,
            max_tokens=1200 if len(chunks) > 1 else 2200,
        )
        if partial.error is not None:
            raise UpstreamFailedError(partial.error.message)
        if len(chunks) == 1:
            response = partial
            break
        partials.append(partial.text)
        _record_response_cost(state, partial)
    else:
        synthesis = _fit_text_to_token_budget(
            "\n\n".join(f"## 分片 {index + 1}\n{value}" for index, value in enumerate(partials)),
            input_budget,
        )
        response = await _model_call(
            db,
            project_id,
            (
                "合并以下分片分析，输出一份去重、统一、可审核的文风档案。必须包含"
                "叙事视角、句式长度、节奏、描写密度、对白特点、常用意象、应避免事项"
                "和可执行文风规则。\n\n" + synthesis
            ),
            profile,
            use_demo=use_demo_model,
            max_tokens=2200,
        )
    if response.error is not None:
        raise UpstreamFailedError(response.error.message)
    metadata = {
        "agent_name": "参考文风分析",
        "filename": filename,
        "model": profile.display_name if profile else "内置演示模型",
        "model_reason": reason,
        "series_key": "world:style-reference",
        "reference_characters": len(text),
        "context_chunks": len(chunks),
        "context_strategy": "chunked_style_analysis" if len(chunks) > 1 else "direct",
    }
    _supersede_series(db, project_id, str(metadata["series_key"]))
    artifact = _new_artifact(
        project_id, "world", f"参考文风分析 · {filename}", response.text, metadata, 90
    )
    _mark_conflicts(artifact)
    db.add(artifact)
    _record_response_cost(state, response)
    _apply_budget_after_task(state)
    db.commit()
    return _artifact_record(artifact)


def _select_model(
    db: Session, state: models.StudioProjectState, use_demo: bool
) -> tuple[models.ModelProfile | None, str]:
    if use_demo:
        return None, "用户选择了内置演示模型；不会访问付费 API。"
    profiles = db.scalars(
        select(models.ModelProfile)
        .join(
            models.ProviderAccount,
            models.ProviderAccount.id == models.ModelProfile.provider_account_id,
        )
        .where(
            models.ModelProfile.enabled.is_(True),
            models.ModelProfile.deleted_at.is_(None),
            models.ProviderAccount.enabled.is_(True),
            models.ProviderAccount.deleted_at.is_(None),
            models.ProviderAccount.provider_type.not_in(["mock", "ollama", "ollama_native"]),
        )
    ).all()
    profiles = [
        profile for profile in profiles if _provider_has_key(db, profile.provider_account_id)
    ]
    if not profiles:
        raise ConflictError("尚未配置可用 API，请先前往“模型与 API”添加密钥")
    strategy = state.routing_strategy
    if strategy == "quality":
        chosen = max(profiles, key=lambda item: item.context_window)
        reason = "质量优先：选择了已配置模型中上下文容量最高的模型。"
    elif strategy == "speed":
        chosen = min(profiles, key=lambda item: _latency(db, item.provider_account_id))
        reason = "速度优先：选择了最近健康记录中延迟最低的模型。"
    elif strategy == "cost":
        chosen = min(profiles, key=lambda item: _price_score(db, item.id))
        reason = "成本优先：选择了当前已知输入与输出单价最低的模型。"
    else:
        chosen = max(
            profiles,
            key=lambda item: (
                (item.context_window / max(_price_score(db, item.id), 0.01))
                / max(_latency(db, item.provider_account_id), 100)
            ),
        )
        reason = "均衡模式：综合上下文容量、已知价格和最近延迟自动选择。"
    return chosen, reason


def _provider_has_key(db: Session, provider_id: int) -> bool:
    provider = db.get(models.ProviderAccount, provider_id)
    if provider is None:
        return False
    if provider.credential_env_var:
        import os

        if os.getenv(provider.credential_env_var):
            return True
    try:
        return has_provider_secret(provider_id)
    except OSError:
        return False


async def _model_call(
    db: Session,
    project_id: int,
    prompt: str,
    profile: models.ModelProfile | None,
    *,
    use_demo: bool,
    max_tokens: int = 2200,
) -> Any:
    output_tokens = _effective_output_tokens(profile, use_demo, max_tokens)
    input_budget = _studio_input_budget(profile, use_demo, max_tokens)
    original_prompt = prompt
    compression_warnings: list[str] = []
    response: Any = None
    for attempt in range(5):
        attempt_budget = max(128, input_budget // (2**attempt))
        fitted_prompt = _fit_text_to_token_budget(original_prompt, attempt_budget)
        if fitted_prompt != original_prompt:
            compression_warnings.append(
                f"上下文已自动压缩至约 {estimate_text_tokens(fitted_prompt)} Token。"
            )
        payload = ModelDebugRequest(
            model="mock-novel-v1" if use_demo or profile is None else profile.name,
            model_profile_id=None if use_demo or profile is None else profile.id,
            project_id=project_id,
            messages=[
                NormalizedMessage(
                    role="user",
                    content=[NormalizedContentPart(type="text", text=fitted_prompt)],
                )
            ],
            max_tokens=output_tokens,
            temperature=0.75,
            max_retries=5,
            allow_degradation=True,
        )
        response = await model_execution.execute_model(db, payload)
        error = getattr(response, "error", None)
        if error is None or getattr(error, "code", "") != "context_too_long":
            break
        if attempt < 4:
            compression_warnings.append("Provider 返回上下文超限，已进一步压缩并自动重试。")
    if response is not None and compression_warnings:
        response.warnings = list(
            dict.fromkeys([*getattr(response, "warnings", []), *compression_warnings])
        )
    return response


def _effective_output_tokens(
    profile: models.ModelProfile | None, use_demo: bool, requested: int
) -> int:
    window = 8_192 if use_demo or profile is None else max(512, profile.context_window)
    proportional_limit = max(256, int(window * 0.4))
    return max(1, min(requested, proportional_limit, max(1, window - 256)))


def _studio_input_budget(
    profile: models.ModelProfile | None, use_demo: bool, max_tokens: int
) -> int:
    window = 8_192 if use_demo or profile is None else max(512, profile.context_window)
    output_tokens = _effective_output_tokens(profile, use_demo, max_tokens)
    safety = 384 if window >= 1_024 else 64
    return max(128, window - output_tokens - safety)


def _fit_text_to_token_budget(text: str, token_budget: int) -> str:
    if token_budget <= 0 or not text:
        return ""
    if estimate_text_tokens(text) <= token_budget:
        return text
    marker = "\n\n[上下文已自动压缩：省略中间低优先级内容]\n\n"
    if estimate_text_tokens(marker) >= token_budget:
        return text[: _prefix_index_for_tokens(text, token_budget)].rstrip()
    low = 0
    high = len(text)
    best = marker.strip()
    while low <= high:
        keep = (low + high) // 2
        head_count = int(keep * 0.68)
        tail_count = keep - head_count
        candidate = (
            text[:head_count].rstrip()
            + marker
            + (text[-tail_count:].lstrip() if tail_count else "")
        )
        if estimate_text_tokens(candidate) <= token_budget:
            best = candidate
            low = keep + 1
        else:
            high = keep - 1
    return best


def _prefix_index_for_tokens(text: str, token_budget: int) -> int:
    low = 0
    high = len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if estimate_text_tokens(text[:middle]) <= token_budget:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _chunk_text_by_tokens(text: str, token_budget: int) -> list[str]:
    if token_budget < 128:
        raise ValueError("分片 Token 预算不能低于 128")
    remaining = text.strip()
    if not remaining:
        return [""]
    chunks: list[str] = []
    while estimate_text_tokens(remaining) > token_budget:
        cut = _prefix_index_for_tokens(remaining, token_budget)
        if cut <= 0:
            cut = 1
        line_cut = remaining.rfind("\n", max(0, cut // 2), cut)
        if line_cut > 0:
            cut = line_cut
        chunk = remaining[:cut].strip()
        if not chunk:
            chunk = remaining[:cut]
        chunks.append(chunk)
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _phase_output_tokens(phase: str) -> int:
    if phase == "drafting":
        return 3_600
    if phase == "chapters":
        return 5_200
    return 2_200


def _phase_prompt(
    project: models.Project,
    phase: str,
    agent_name: str,
    responsibility: str,
    context: str,
    payload: GenerateRequest,
    upstream: list[str],
) -> str:
    format_hint = ""
    if phase == "chapters":
        format_hint = (
            "必须使用可解析的 Markdown 层级：# 第N卷 卷名、## 第N章 章名、"
            "### 场景N 场景名；每个标题下写目标、冲突、转折和结果。\n"
        )
    elif phase in {
        "world",
        "characters",
        "plot",
        "volumes",
        "continuation_analysis",
        "continuation_outline",
        "continuation_plan",
    }:
        format_hint = "使用清晰的 Markdown 小节逐项输出，确保每项可以独立修改。\n"
    if phase == "continuation_plan" and agent_name == "未来卷章规划":
        format_hint = (
            "必须使用可解析的 Markdown 层级：# 第N卷 卷名、## 第N章 章名、"
            "### 场景N 场景名；只规划原文之后的未来章节。\n"
        )
    if phase == "drafting":
        format_hint = (
            "只输出可直接写入小说的正文，不要输出分析、标题、Markdown 标记或创作说明。"
            + (
                "从当前章节最后一句自然接续，不要重写已有段落。\n"
                if payload.mode == "continue"
                else "\n"
            )
        )
    return (
        f"你是多智能体小说工作室中的“{agent_name}”。{responsibility}\n"
        "请输出可供作者逐项审核和直接修改的中文内容。信息要具体，不要讲解工作方法。"
        "不得擅自推翻已批准内容；发现冲突时明确标注冲突级别和建议。\n\n"
        f"{format_hint}"
        f"小说：{project.title}\n创意：{project.summary}\n阶段：{STAGE_LABELS.get(phase, phase)}\n"
        f"作者补充要求：{payload.instruction or '无'}\n"
        f"自动检索的项目上下文：\n{context}\n\n"
        f"同阶段上游 Agent 输出：\n{chr(10).join(upstream[-2:]) if upstream else '无'}\n\n"
        f"选中文本：\n{payload.selected_text or '无'}"
    )


def _generation_context(
    db: Session,
    project_id: int,
    chapter_id: int | None,
    *,
    profile: models.ModelProfile | None,
    use_demo: bool,
    max_tokens: int,
    query: str,
) -> tuple[str, dict[str, Any]]:
    input_budget = _studio_input_budget(profile, use_demo, max_tokens)
    context_budget = max(128, min(6_000, input_budget - min(800, input_budget // 4)))
    artifacts = db.scalars(
        select(models.CreativeArtifact)
        .where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.status == "approved",
            models.CreativeArtifact.deleted_at.is_(None),
        )
        .order_by(models.CreativeArtifact.position, models.CreativeArtifact.id.desc())
    ).all()
    approved = {
        item.title: _fit_text_to_token_budget(item.content, 1_200)
        for item in artifacts[-12:]
        if item.kind != "continuation_original"
    }
    request = ContextBuildRequest(
        project_id=project_id,
        chapter_id=chapter_id,
        model_profile_id=(None if use_demo or profile is None else profile.id),
        model_context_window=(8_192 if use_demo or profile is None else profile.context_window),
        query=query[:200_000],
        upstream_outputs={"approved_artifacts": approved},
        reserved_output_tokens=_effective_output_tokens(profile, use_demo, max_tokens),
        token_budget_override=context_budget,
        persist_snapshot=False,
    )
    built = context_builder.build_context(db, request)
    if not built.blocked and built.context_text.strip():
        return built.context_text, {
            "strategy": "retrieval",
            "model_window": request.model_context_window,
            "token_budget": built.token_budget,
            "included_tokens": built.included_tokens,
            "included_items": len(built.included),
            "excluded_items": len(built.excluded),
            "truncations": len(built.truncations),
        }

    blocks = [
        f"[{item.title}]\n{_fit_text_to_token_budget(item.content, 900)}"
        for item in artifacts[-8:]
        if item.kind != "continuation_original"
    ]
    state = _studio_state(db, project_id)
    if state.entry_mode == "continuation":
        summaries = db.execute(
            select(models.Chapter.title, models.ChapterSummary.summary)
            .join(models.ChapterSummary, models.ChapterSummary.chapter_id == models.Chapter.id)
            .join(models.Volume, models.Volume.id == models.Chapter.volume_id)
            .where(
                models.Volume.project_id == project_id,
                models.Chapter.deleted_at.is_(None),
                models.ChapterSummary.deleted_at.is_(None),
            )
            .order_by(models.Volume.position, models.Chapter.position)
        ).all()
        if summaries:
            summary_text = "\n".join(f"- {title}: {summary}" for title, summary in summaries)
            blocks.append("[导入原文章节索引]\n" + summary_text)
    if chapter_id:
        chapter = db.get(models.Chapter, chapter_id)
        if chapter is not None:
            blocks.append(f"[当前章节：{chapter.title}]\n{chapter.content}")
    entities = db.scalars(
        select(models.StoryEntity)
        .where(
            models.StoryEntity.project_id == project_id,
            models.StoryEntity.deleted_at.is_(None),
        )
        .limit(30)
    ).all()
    if entities:
        blocks.append(
            "[人物与资料]\n"
            + "\n".join(f"- {item.name}: {item.description[:300]}" for item in entities)
        )
    fallback = _fit_text_to_token_budget("\n\n".join(blocks) or "尚无已批准资料。", context_budget)
    return fallback, {
        "strategy": "compressed_fallback",
        "model_window": request.model_context_window,
        "token_budget": context_budget,
        "included_tokens": estimate_text_tokens(fallback),
        "included_items": len(blocks),
        "excluded_items": len(built.excluded),
        "truncations": max(1, len(built.truncations)),
        "conflicts": built.conflicts,
    }


async def _continuation_source_context(
    db: Session,
    project_id: int,
    phase: str,
    profile: models.ModelProfile | None,
    *,
    use_demo: bool,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    input_budget = _studio_input_budget(profile, use_demo, max_tokens)
    chunk_budget = max(512, input_budget - min(900, input_budget // 3))
    corpus = _continuation_corpus(
        db,
        project_id,
        phase,
        total_budget=chunk_budget * 32,
    )
    chunks = _chunk_text_by_tokens(corpus, chunk_budget)
    if len(chunks) == 1:
        return "[导入原文分层索引]\n" + chunks[0], {
            "source_strategy": "hierarchical_index",
            "source_chunks": 1,
        }

    map_outputs: list[str] = []
    for index, chunk in enumerate(chunks):
        task = (
            "从本分片提取卷章结构、世界规则、人物关系与状态、时间线、伏笔、"
            "文风和未完成剧情线。保留章节名称与证据位置，简洁输出。"
            if phase == "continuation_analysis"
            else "根据本分片补建已有分卷、章节和场景的目标、冲突、转折、结果与承接关系。"
        )
        response = await _model_call(
            db,
            project_id,
            (
                f"你正在对半成品小说执行分片预处理。{task}\n"
                f"分片 {index + 1}/{len(chunks)}：\n\n{chunk}"
            ),
            profile,
            use_demo=use_demo,
            max_tokens=min(1_200, max_tokens),
        )
        if response.error is not None:
            raise RuntimeError(f"{response.error.code}: {response.error.message}")
        map_outputs.append(response.text.strip())
        _record_response_cost(_studio_state(db, project_id), response)
    aggregate = "\n\n".join(
        f"## 分片 {index + 1}\n{content}" for index, content in enumerate(map_outputs)
    )
    fitted = _fit_text_to_token_budget(aggregate, max(512, input_budget - 700))
    return "[全书分片分析汇总]\n" + fitted, {
        "source_strategy": "map_reduce",
        "source_chunks": len(chunks),
        "source_summary_tokens": estimate_text_tokens(fitted),
    }


def _continuation_corpus(
    db: Session,
    project_id: int,
    phase: str,
    *,
    total_budget: int,
) -> str:
    rows = db.execute(
        select(
            models.Volume.title,
            models.Chapter.title,
            models.Chapter.content,
            models.ChapterSummary.summary,
        )
        .join(models.Chapter, models.Chapter.volume_id == models.Volume.id)
        .outerjoin(
            models.ChapterSummary,
            (models.ChapterSummary.chapter_id == models.Chapter.id)
            & models.ChapterSummary.deleted_at.is_(None),
        )
        .where(
            models.Volume.project_id == project_id,
            models.Volume.deleted_at.is_(None),
            models.Chapter.deleted_at.is_(None),
        )
        .order_by(models.Volume.position, models.Chapter.position, models.Chapter.id)
    ).all()
    if not rows:
        return "尚无导入章节。"
    per_chapter = max(24, total_budget // len(rows))
    blocks: list[str] = []
    for volume_title, chapter_title, content, summary in rows:
        source = str(summary or "") if phase == "continuation_outline" else str(content or "")
        block = f"# {volume_title} / {chapter_title}\n{source}"
        blocks.append(_fit_text_to_token_budget(block, per_chapter))
    return "\n\n".join(blocks)


def _context_reason(metadata: dict[str, Any]) -> str:
    strategy = str(metadata.get("strategy") or "retrieval")
    included = int(metadata.get("included_tokens") or 0)
    chunks = int(metadata.get("source_chunks") or 0)
    text = f"上下文：{strategy}，约 {included:,} Token"
    if chunks > 1:
        text += f"，原文分为 {chunks} 片汇总"
    if int(metadata.get("truncations") or 0) > 0:
        text += "，已按预算压缩"
    return text + "。"


def _artifact_title(phase: str, payload: GenerateRequest) -> str:
    if payload.mode == "local_revision":
        return "局部修改提案"
    if payload.mode == "full_rewrite":
        return "全文重写提案"
    if payload.mode == "variants":
        return "多方案对比"
    return STAGE_LABELS.get(phase, phase)


def _record_response_cost(state: models.StudioProjectState, response: Any) -> None:
    control = response.control or {}
    amount = 0.0
    for attempt in control.get("attempts", []):
        cost = attempt.get("cost") if isinstance(attempt, dict) else None
        if isinstance(cost, dict) and isinstance(cost.get("amount"), (int, float)):
            amount += float(cost["amount"])
    state.budget_spent += amount
    state.revision += 1


def _apply_budget_after_task(state: models.StudioProjectState) -> None:
    if state.budget_limit and state.budget_spent >= state.budget_limit * (
        state.budget_pause_percent / 100
    ):
        state.budget_paused = True


def _usage_summary(
    db: Session, project_id: int, state: models.StudioProjectState
) -> dict[str, Any]:
    invocations = int(
        db.scalar(
            select(func.count(models.ModelInvocation.id)).where(
                models.ModelInvocation.project_id == project_id
            )
        )
        or 0
    )
    tokens = int(
        db.scalar(
            select(func.coalesce(func.sum(models.ModelInvocation.total_tokens), 0)).where(
                models.ModelInvocation.project_id == project_id
            )
        )
        or 0
    )
    percent = (
        state.budget_spent / state.budget_limit * 100
        if state.budget_limit and state.budget_limit > 0
        else 0
    )
    return {
        "invocations": invocations,
        "tokens": tokens,
        "spent": state.budget_spent,
        "limit": state.budget_limit,
        "currency": state.budget_currency,
        "percent": round(percent, 2),
        "warning": percent >= state.budget_warning_percent,
        "paused": state.budget_paused,
    }
