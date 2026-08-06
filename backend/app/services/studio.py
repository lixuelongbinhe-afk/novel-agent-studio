from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, load_only

from app import models
from app.services.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
)
from app.repositories import word_count
from app.schemas.studio import (
    ArtifactDecision,
    ArtifactUpdate,
    ContinuationImportRequest,
    ContinuationSettingsUpdate,
    DashboardProjectRead,
    ProjectOverviewRead,
    SnapshotCreate,
    StudioProjectCreate,
    StudioStateUpdate,
)
from app.services import chapter_tree as _chapter_tree
from app.services import studio_generation as _studio_generation
from app.services import studio_providers as _studio_providers
from app.services.chapter_plans import (
    chapter_title_number as _chapter_title_number,
    clean_outline_label as _clean_outline_label,
    volume_title_number as _volume_title_number,
)


STAGE_ORDER = [
    "idea",
    "world",
    "characters",
    "plot",
    "volumes",
    "chapters",
    "drafting",
    "review",
    "complete",
]
CONTINUATION_STAGE_ORDER = [
    "continuation_import",
    "continuation_analysis",
    "continuation_outline",
    "continuation_plan",
    "drafting",
    "review",
    "complete",
]
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
PHASE_AGENTS: dict[str, list[tuple[str, str]]] = {
    "continuation_analysis": [
        ("章节结构分析", "核对已导入卷章结构、叙事进度与当前断点，指出识别不确定项。"),
        ("世界观提取", "从原文提取世界规则、时代背景、地点体系、组织与约束。"),
        ("人物关系提取", "提取人物身份、目标、秘密、关系、当前状态与人物弧光。"),
        ("时间线提取", "整理已发生事件的时间顺序、因果关系和时间线疑点。"),
        ("伏笔与线索提取", "整理已埋设、已发展、已回收和仍待回收的伏笔与线索。"),
        ("原文文风档案", "提取叙事视角、句式节奏、对白习惯、用词尺度与描写密度。"),
        ("未完剧情线", "识别主线、支线、角色目标、悬念和尚未解决的剧情承诺。"),
    ],
    "continuation_outline": [
        ("既有分卷大纲", "根据已导入正文反向补建已有分卷的大纲、目标与转折。"),
        ("既有章节大纲", "逐章反向补建目标、冲突、转折、结果和承接关系。"),
        ("既有场景大纲", "为已有章节补建场景顺序、视角、地点、人物和场景结果。"),
    ],
    "continuation_plan": [
        ("续写方向与结局", "结合作者方向与未完剧情线，提出可审核的后续走向和结局方案。"),
        ("未来卷章规划", "规划未来分卷、章节和场景，衔接原文断点并完成全部剧情承诺。"),
    ],
    "world": [
        ("定位与主题策划", "明确题材定位、目标读者、核心主题、叙事基调与篇幅策略。"),
        ("世界观架构师", "建立自洽的世界规则、时代背景、地点体系与核心矛盾。"),
        ("规则审校员", "检查世界规则的边界、代价、漏洞与可持续冲突。"),
        ("文风与边界编辑", "确定叙事视角、句式、节奏、描写密度和禁止内容边界。"),
    ],
    "characters": [
        ("人物设计师", "设计主要人物的目标、恐惧、秘密、弧光与辨识度。"),
        ("关系审校员", "建立人物关系网，指出利益冲突、情感张力和关系变化节点。"),
    ],
    "plot": [
        ("剧情架构师", "设计主线、支线、关键转折、高潮和结局逻辑。"),
        ("伏笔设计师", "安排可追踪的伏笔、误导、揭示和回收章节。"),
        ("连贯性审校员", "检查人物动机、时间线与因果链，列出重大风险。"),
    ],
    "volumes": [
        ("分卷策划", "将故事拆分为有独立目标和结尾钩子的分卷。"),
        ("节奏编辑", "检查各卷的推进速度、信息密度和情绪曲线。"),
    ],
    "chapters": [
        ("章节规划师", "逐章给出标题、目标、冲突、转折和结尾钩子。"),
        ("场景规划师", "为每章拆分场景，说明视角、地点、出场人物和场景结果。"),
    ],
    "drafting": [
        ("小说主笔", "按已批准设定和大纲写出可直接审阅的正文。"),
        ("对白与文风审校", "修正对白辨识度、叙事视角、节奏和文风偏移。"),
        ("连贯性总编", "检查与前文、人物状态、时间线和伏笔的一致性。"),
    ],
    "review": [
        ("终稿编辑", "检查结构、重复、节奏、语言和未回收线索。"),
        ("一致性审校", "检查人物、地点、时间线、规则和事实冲突。"),
    ],
}
AGENT_HEADINGS = {name for agents in PHASE_AGENTS.values() for name, _ in agents}

_approved_volume_titles = _chapter_tree._approved_volume_titles
_chapter_generation_ranges = _chapter_tree._chapter_generation_ranges
_chapter_plan_excerpt = _chapter_tree._chapter_plan_excerpt
_chapter_plan_validation = _chapter_tree._chapter_plan_validation
_chapter_volume_starts = _chapter_tree._chapter_volume_starts
_create_imported_manuscript_tree = _chapter_tree._create_imported_manuscript_tree
_default_chapter_scenes = _chapter_tree._default_chapter_scenes
_ensure_chapter_tree_from_plan = _chapter_tree._ensure_chapter_tree_from_plan
_ensure_continuation_tree_from_plan = _chapter_tree._ensure_continuation_tree_from_plan
_format_number_ranges = _chapter_tree._format_number_ranges
_is_placeholder_chapter_title = _chapter_tree._is_placeholder_chapter_title
_merge_parsed_volumes = _chapter_tree._merge_parsed_volumes
_missing_chapter_numbers = _chapter_tree._missing_chapter_numbers
_normalize_generated_chapter_plan = _chapter_tree._normalize_generated_chapter_plan
_reconcile_chapter_scenes = _chapter_tree._reconcile_chapter_scenes
_reconcile_chapter_tree = _chapter_tree._reconcile_chapter_tree
_render_chapter_plan = _chapter_tree._render_chapter_plan
_restore_tree = _chapter_tree._restore_tree
_validate_chapter_plan_approval = _chapter_tree._validate_chapter_plan_approval
_volume_index_for_chapter = _chapter_tree._volume_index_for_chapter
_volume_plan_key = _chapter_tree._volume_plan_key
chapter_tree_repair_preview = _chapter_tree.chapter_tree_repair_preview
import_outline = _chapter_tree.import_outline
parse_outline = _chapter_tree.parse_outline
repair_chapter_tree = _chapter_tree.repair_chapter_tree

_apply_budget_after_task = _studio_generation._apply_budget_after_task
_artifact_title = _studio_generation._artifact_title
_chunk_text_by_tokens = _studio_generation._chunk_text_by_tokens
_context_reason = _studio_generation._context_reason
_continuation_corpus = _studio_generation._continuation_corpus
_continuation_source_context = _studio_generation._continuation_source_context
_effective_output_tokens = _studio_generation._effective_output_tokens
_fit_text_to_token_budget = _studio_generation._fit_text_to_token_budget
_generation_context = _studio_generation._generation_context
_maybe_finish_drafting = _studio_generation._maybe_finish_drafting
_model_call = _studio_generation._model_call
_phase_output_tokens = _studio_generation._phase_output_tokens
_phase_prompt = _studio_generation._phase_prompt
_prefix_index_for_tokens = _studio_generation._prefix_index_for_tokens
_provider_has_key = _studio_generation._provider_has_key
_record_response_cost = _studio_generation._record_response_cost
_require_generation_prerequisites = _studio_generation._require_generation_prerequisites
_select_model = _studio_generation._select_model
_studio_input_budget = _studio_generation._studio_input_budget
_usage_summary = _studio_generation._usage_summary
extract_style_reference = _studio_generation.extract_style_reference
generate = _studio_generation.generate
chat = _studio_generation.chat
decide_message_proposal = _studio_generation.decide_message_proposal
_record = _studio_providers._record
_mark_conflicts = _studio_generation._mark_conflicts
_new_artifact = _studio_generation._new_artifact
_phase_complete = _studio_generation._phase_complete
_supersede_series = _studio_generation._supersede_series


def create_project(db: Session, payload: StudioProjectCreate) -> ProjectOverviewRead:
    project = models.Project(
        title=payload.title.strip(),
        summary=payload.idea.strip(),
        language="zh-CN",
        target_words=payload.target_words,
    )
    db.add(project)
    db.flush()
    config = payload.model_dump(exclude={"title", "idea", "entry_mode", "target_words"})
    state = models.StudioProjectState(
        project_id=project.id,
        entry_mode=payload.entry_mode,
        stage="idea" if payload.entry_mode == "creative" else "chapters",
        config_json=_dump(config),
    )
    db.add(state)
    db.add(
        models.CreativeArtifact(
            project_id=project.id,
            kind="idea",
            title="创意简报" if payload.entry_mode == "creative" else "导入大纲说明",
            content=payload.idea.strip(),
            status="approved",
            source="user",
        )
    )
    from app.services.context_memory import ensure_default_context_policy

    ensure_default_context_policy(db, project.id)
    db.flush()
    return project_overview(db, project.id)


def create_continuation_project(
    db: Session, payload: ContinuationImportRequest
) -> ProjectOverviewRead:
    if payload.source_project_id is not None:
        source_text, source_name = _source_project_manuscript(db, payload.source_project_id)
    else:
        source_text = str(payload.text or "").strip()
        source_name = payload.source_name.strip() or "粘贴正文"
    if not source_text:
        raise InvalidInputError("导入正文不能为空")

    parsed = parse_manuscript(source_text, payload.title.strip())
    imported_words = word_count(source_text)
    target_words = payload.target_words or max(imported_words + 50_000, imported_words * 2)
    project = models.Project(
        title=payload.title.strip(),
        summary=f"从《{source_name}》导入的半成品小说，共 {parsed['chapter_count']} 章。",
        language="zh-CN",
        target_words=target_words,
    )
    db.add(project)
    db.flush()
    config = {
        "source_name": source_name,
        "source_type": "project" if payload.source_project_id is not None else "text",
        "source_project_id": payload.source_project_id,
        "imported_words": imported_words,
        "imported_chapter_count": parsed["chapter_count"],
        "imported_volume_count": parsed["volume_count"],
        "target_words": payload.target_words,
        "target_chapters": payload.target_chapters,
        "target_volumes": payload.target_volumes,
        "target_mode": "manual"
        if any(
            value is not None
            for value in (payload.target_words, payload.target_chapters, payload.target_volumes)
        )
        else "ai",
        "continuation_start": payload.continuation_start,
        "direction_mode": payload.direction_mode,
        "user_outline": payload.user_outline.strip(),
        "conflict_paused": False,
    }
    state = models.StudioProjectState(
        project_id=project.id,
        entry_mode="continuation",
        stage="continuation_analysis",
        config_json=_dump(config),
    )
    db.add(state)
    original = models.CreativeArtifact(
        project_id=project.id,
        kind="continuation_original",
        title=f"原始只读副本 · {source_name}",
        content=source_text,
        status="approved",
        source="import",
        position=0,
        metadata_json=_dump(
            {
                "readonly": True,
                "permanent": True,
                "source_name": source_name,
                "characters": len(source_text),
                "words": imported_words,
                "series_key": "continuation:original",
            }
        ),
    )
    db.add(original)
    if payload.user_outline.strip():
        db.add(
            models.CreativeArtifact(
                project_id=project.id,
                kind="continuation_direction",
                title="作者提供的后续方向",
                content=payload.user_outline.strip(),
                status="approved",
                source="user",
                position=310,
                metadata_json=_dump({"series_key": "continuation:user-direction"}),
            )
        )
    _create_imported_manuscript_tree(db, project.id, parsed)
    from app.services.context_memory import ensure_default_context_policy

    ensure_default_context_policy(db, project.id)
    db.flush()
    create_snapshot(
        db,
        project.id,
        SnapshotCreate(
            label="半成品原文导入完成",
            reason="永久保存导入原文、识别出的卷章结构和可编辑正文副本",
            special=True,
        ),
    )
    db.flush()
    return project_overview(db, project.id)


def update_continuation_settings(
    db: Session,
    project_id: int,
    payload: ContinuationSettingsUpdate,
) -> dict[str, Any]:
    state = _state(db, project_id)
    if state.entry_mode != "continuation":
        raise ConflictError("该项目不是半成品续写项目")
    config = _json_object(state.config_json)
    for key, value in payload.model_dump(exclude_none=True).items():
        config[key] = value.strip() if isinstance(value, str) else value
    if any(
        key in payload.model_fields_set
        for key in ("target_words", "target_chapters", "target_volumes")
    ):
        config["target_mode"] = "manual"
    if payload.target_words is not None:
        _project(db, project_id).target_words = payload.target_words
    state.config_json = _dump(config)
    state.revision += 1
    db.flush()
    return _state_record(state)


def parse_manuscript(text: str, title: str = "导入小说") -> dict[str, Any]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    volumes: list[dict[str, Any]] = []
    current_volume: dict[str, Any] | None = None
    current_title: str | None = None
    body: list[str] = []

    def ensure_volume() -> dict[str, Any]:
        nonlocal current_volume
        if current_volume is None:
            current_volume = {"title": "第一卷", "chapters": []}
            volumes.append(current_volume)
        return current_volume

    def flush_chapter() -> None:
        nonlocal current_title, body
        content = "\n".join(body).strip()
        if current_title is not None or content:
            volume = ensure_volume()
            volume["chapters"].append(
                {
                    "title": current_title or ("序章" if not volume["chapters"] else "未命名章节"),
                    "content": content,
                }
            )
        current_title = None
        body = []

    for raw_line in normalized.splitlines():
        stripped = raw_line.strip()
        heading = _clean_outline_label(re.sub(r"^#{1,6}\s+", "", stripped).strip())
        if _volume_title_number(heading) is not None:
            flush_chapter()
            current_volume = {"title": heading, "chapters": []}
            volumes.append(current_volume)
            continue
        if _chapter_title_number(heading) is not None:
            flush_chapter()
            ensure_volume()
            current_title = heading
            continue
        body.append(raw_line)
    flush_chapter()
    volumes = _merge_parsed_volumes([volume for volume in volumes if volume["chapters"]])
    if not volumes:
        volumes = [{"title": "第一卷", "chapters": [{"title": "第一章", "content": normalized}]}]
    chapter_count = sum(len(volume["chapters"]) for volume in volumes)
    return {
        "title": title,
        "volumes": volumes,
        "volume_count": len(volumes),
        "chapter_count": chapter_count,
        "word_count": word_count(normalized),
        "warnings": ["只识别到一个章节，请确认原文中的章节标题格式。"]
        if chapter_count == 1
        else [],
    }


def _source_project_manuscript(db: Session, project_id: int) -> tuple[str, str]:
    source = _project(db, project_id)
    volumes = list(
        db.scalars(
            select(models.Volume)
            .where(models.Volume.project_id == project_id, models.Volume.deleted_at.is_(None))
            .order_by(models.Volume.position, models.Volume.id)
        ).all()
    )
    blocks: list[str] = []
    for volume in volumes:
        blocks.append(f"# {volume.title}")
        chapters = db.scalars(
            select(models.Chapter)
            .where(models.Chapter.volume_id == volume.id, models.Chapter.deleted_at.is_(None))
            .order_by(models.Chapter.position, models.Chapter.id)
        ).all()
        for chapter in chapters:
            blocks.extend((f"## {chapter.title}", chapter.content))
    text = "\n\n".join(blocks).strip()
    if not text:
        raise ConflictError("所选项目没有可导入的正文")
    return text, source.title


def dashboard(db: Session) -> list[DashboardProjectRead]:
    projects = db.scalars(
        select(models.Project)
        .where(models.Project.deleted_at.is_(None))
        .order_by(models.Project.updated_at.desc(), models.Project.id.desc())
    ).all()
    result: list[DashboardProjectRead] = []
    for project in projects:
        state = _state(db, project.id)
        volume_ids = select(models.Volume.id).where(
            models.Volume.project_id == project.id,
            models.Volume.deleted_at.is_(None),
        )
        completed_words = int(
            db.scalar(
                select(func.coalesce(func.sum(models.Chapter.word_count), 0)).where(
                    models.Chapter.volume_id.in_(volume_ids),
                    models.Chapter.deleted_at.is_(None),
                )
            )
            or 0
        )
        pending = int(
            db.scalar(
                select(func.count(models.CreativeArtifact.id)).where(
                    models.CreativeArtifact.project_id == project.id,
                    models.CreativeArtifact.status.in_(["pending", "changes_requested"]),
                    models.CreativeArtifact.deleted_at.is_(None),
                )
            )
            or 0
        )
        result.append(
            DashboardProjectRead(
                id=project.id,
                title=project.title,
                summary=project.summary,
                stage=state.stage,
                stage_label=STAGE_LABELS.get(state.stage, state.stage),
                completed_words=completed_words,
                target_words=project.target_words,
                pending_reviews=pending,
                updated_at=project.updated_at.isoformat(),
                entry_mode=state.entry_mode,
            )
        )
    return result


def mark_interrupted_generation_jobs(db: Session) -> int:
    jobs = db.scalars(
        select(models.GenerationJob).where(
            models.GenerationJob.status.in_(["queued", "running"]),
            models.GenerationJob.deleted_at.is_(None),
        )
    ).all()
    for job in jobs:
        job.status = "failed"
        job.progress = 100
        job.error_message = "应用在任务完成前退出；部分输出未写入，请重新生成。"
        job.active_scope_key = None
        job.revision += 1
    db.flush()
    return len(jobs)


def project_overview(db: Session, project_id: int) -> ProjectOverviewRead:
    project = _project(db, project_id)
    state = _state(db, project_id)
    volumes = db.scalars(
        select(models.Volume)
        .where(models.Volume.project_id == project_id, models.Volume.deleted_at.is_(None))
        .order_by(models.Volume.position, models.Volume.id)
    ).all()
    volume_ids = [item.id for item in volumes]
    chapters = db.scalars(
        select(models.Chapter)
        .where(
            models.Chapter.volume_id.in_(volume_ids or [-1]),
            models.Chapter.deleted_at.is_(None),
        )
        .order_by(models.Chapter.volume_id, models.Chapter.position, models.Chapter.id)
    ).all()
    volume_order = {volume.id: index for index, volume in enumerate(volumes)}
    chapters = sorted(
        chapters,
        key=lambda chapter: (
            volume_order.get(chapter.volume_id, len(volume_order)),
            chapter.position,
            chapter.id,
        ),
    )
    chapter_ids = [item.id for item in chapters]
    scenes = db.scalars(
        select(models.Scene)
        .where(
            models.Scene.chapter_id.in_(chapter_ids or [-1]),
            models.Scene.deleted_at.is_(None),
        )
        .order_by(models.Scene.chapter_id, models.Scene.position, models.Scene.id)
    ).all()
    artifacts = db.scalars(
        select(models.CreativeArtifact)
        .where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.status != "superseded",
            models.CreativeArtifact.deleted_at.is_(None),
        )
        .order_by(models.CreativeArtifact.position, models.CreativeArtifact.id.desc())
    ).all()
    jobs = db.scalars(
        select(models.GenerationJob)
        .where(
            models.GenerationJob.project_id == project_id,
            models.GenerationJob.deleted_at.is_(None),
        )
        .order_by(models.GenerationJob.id.desc())
        .limit(30)
    ).all()
    messages = db.scalars(
        select(models.StudioMessage)
        .where(models.StudioMessage.project_id == project_id)
        .order_by(models.StudioMessage.id.desc())
        .limit(80)
    ).all()
    snapshots = db.scalars(
        select(models.ProjectSnapshot)
        .options(
            load_only(
                models.ProjectSnapshot.id,
                models.ProjectSnapshot.project_id,
                models.ProjectSnapshot.kind,
                models.ProjectSnapshot.label,
                models.ProjectSnapshot.reason,
                models.ProjectSnapshot.permanent,
                models.ProjectSnapshot.created_at,
            )
        )
        .where(models.ProjectSnapshot.project_id == project_id)
        .order_by(models.ProjectSnapshot.created_at.desc(), models.ProjectSnapshot.id.desc())
    ).all()
    library_counts = {
        "entities": _count(db, models.StoryEntity, project_id),
        "timeline": _count(db, models.TimelineEvent, project_id),
        "foreshadows": _count(db, models.Foreshadow, project_id),
        "style_guides": _count(db, models.StyleGuide, project_id),
    }
    return ProjectOverviewRead.model_validate(
        {
            "project": _record(project),
            "state": _state_record(state),
            "stages": [{"key": key, "label": STAGE_LABELS[key]} for key in _stage_order(state)],
            "artifacts": [_artifact_record(item) for item in artifacts],
            "tree": {
                "volumes": [_record(item) for item in volumes],
                "chapters": [_record(item) for item in chapters],
                "scenes": [_record(item) for item in scenes],
            },
            "jobs": [_record(item) for item in jobs],
            "messages": [_message_record(item) for item in reversed(messages)],
            "snapshots": [_snapshot_record(item) for item in snapshots],
            "chapter_tree_repair": chapter_tree_repair_preview(db, project_id),
            "library_counts": library_counts,
            "usage": _usage_summary(db, project_id, state),
        }
    )


def update_state(db: Session, project_id: int, payload: StudioStateUpdate) -> dict[str, Any]:
    state = _state(db, project_id)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(state, key, value)
    state.revision += 1
    db.flush()
    return _state_record(state)


def update_artifact(db: Session, artifact_id: int, payload: ArtifactUpdate) -> dict[str, Any]:
    current = _artifact(db, artifact_id)
    if _json_object(current.metadata_json).get("readonly"):
        raise ConflictError("原始导入副本为永久只读内容，不能修改")
    _require_revision(current, payload.expected_revision)
    current.status = "superseded"
    current.revision += 1
    replacement = models.CreativeArtifact(
        project_id=current.project_id,
        kind=current.kind,
        title=payload.title if payload.title is not None else current.title,
        content=payload.content if payload.content is not None else current.content,
        status="pending",
        source="user",
        position=current.position,
        version_number=current.version_number + 1,
        notes=payload.notes if payload.notes is not None else current.notes,
        metadata_json=current.metadata_json,
    )
    replacement_metadata = _json_object(replacement.metadata_json)
    if (
        replacement.kind == "chapters"
        and str(replacement_metadata.get("agent_name") or replacement.title) == "章节规划师"
    ):
        state = _state(db, replacement.project_id)
        requested = max(
            1,
            min(
                int(_json_object(state.config_json).get("chapter_count") or 12),
                10_000,
            ),
        )
        validation = _chapter_plan_validation(
            replacement.content,
            requested,
            _approved_volume_titles(db, replacement.project_id),
        )
        replacement.content = str(validation["preview_markdown"])
        replacement_metadata["chapter_plan_validation"] = {
            key: value
            for key, value in validation.items()
            if key not in {"volumes", "preview_markdown"}
        }
        replacement_metadata["normalized_preview"] = True
        replacement.metadata_json = _dump(replacement_metadata)
    db.add(replacement)
    db.flush()
    return _artifact_record(replacement)


def artifact_versions(db: Session, artifact_id: int) -> list[dict[str, Any]]:
    current = _artifact(db, artifact_id)
    metadata = _json_object(current.metadata_json)
    stored_series_key = str(metadata.get("series_key") or "")
    series_expression = func.json_extract(models.CreativeArtifact.metadata_json, "$.series_key")
    statement = select(models.CreativeArtifact).where(
        models.CreativeArtifact.project_id == current.project_id,
        models.CreativeArtifact.kind == current.kind,
    )
    if stored_series_key:
        statement = statement.where(series_expression == stored_series_key)
    else:
        statement = statement.where(
            models.CreativeArtifact.title == current.title,
            or_(series_expression.is_(None), series_expression == ""),
        )
    candidates = db.scalars(
        statement.order_by(
            models.CreativeArtifact.version_number.desc(),
            models.CreativeArtifact.id.desc(),
        )
    ).all()
    return [_artifact_record(item) for item in candidates]


def decide_artifact(db: Session, artifact_id: int, payload: ArtifactDecision) -> dict[str, Any]:
    artifact = _artifact(db, artifact_id)
    _require_revision(artifact, payload.expected_revision)
    artifact.notes = payload.note
    metadata = _json_object(artifact.metadata_json)
    if payload.action == "approve" and metadata.get("conflict_level") == "major":
        if payload.conflict_resolution is None:
            raise ConflictError("重大冲突必须由作者选择处理方式")
        metadata["conflict_resolution"] = payload.conflict_resolution
        artifact.metadata_json = _dump(metadata)
        if payload.conflict_resolution == "preserve_canon":
            artifact.status = "rejected"
            artifact.notes = (payload.note + "\n保留既有设定，未写入候选正文。").strip()
            artifact.revision += 1
            db.flush()
            _refresh_continuation_conflict_pause(db, artifact.project_id)
            _advance_stage(db, artifact)
            return _artifact_record(artifact)
        if payload.conflict_resolution == "manual_merge" and artifact.source != "user":
            raise ConflictError("请先编辑合并内容并保存新版本，再选择手工合并")
    if payload.action == "approve":
        if artifact.kind == "chapters":
            _validate_chapter_plan_approval(db, artifact)
        artifact.status = "approved"
        _apply_artifact(db, artifact)
        db.flush()
        _advance_stage(db, artifact)
    elif payload.action == "request_changes":
        artifact.status = "changes_requested"
    else:
        artifact.status = "rejected"
    artifact.revision += 1
    db.flush()
    if payload.action in {"approve", "reject"}:
        _refresh_continuation_conflict_pause(db, artifact.project_id)
    return _artifact_record(artifact)


def create_snapshot(db: Session, project_id: int, payload: SnapshotCreate) -> dict[str, Any]:
    _project(db, project_id)
    snapshot = models.ProjectSnapshot(
        project_id=project_id,
        kind="special" if payload.special else "automatic",
        label=payload.label,
        reason=payload.reason,
        permanent=payload.special,
        payload_json=_dump(_snapshot_payload(db, project_id)),
    )
    db.add(snapshot)
    db.flush()
    if not payload.special:
        ordinary = db.scalars(
            select(models.ProjectSnapshot)
            .where(
                models.ProjectSnapshot.project_id == project_id,
                models.ProjectSnapshot.permanent.is_(False),
            )
            .order_by(models.ProjectSnapshot.created_at.desc(), models.ProjectSnapshot.id.desc())
        ).all()
        for stale in ordinary[3:]:
            db.delete(stale)
    db.flush()
    return _snapshot_record(snapshot)


def restore_snapshot(db: Session, project_id: int, snapshot_id: int) -> ProjectOverviewRead:
    snapshot = db.get(models.ProjectSnapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise NotFoundError("项目快照不存在")
    create_snapshot(
        db,
        project_id,
        SnapshotCreate(label="恢复快照前", reason=f"恢复至：{snapshot.label}"),
    )
    payload = _json_object(snapshot.payload_json)
    project = _project(db, project_id)
    project_data = cast(dict[str, Any], payload.get("project") or {})
    project.title = str(project_data.get("title") or project.title)
    project.summary = str(project_data.get("summary") or "")
    project.target_words = int(project_data.get("target_words") or project.target_words)
    project.revision += 1
    state_data = cast(dict[str, Any], payload.get("state") or {})
    state = _state(db, project_id)
    for key in (
        "entry_mode",
        "stage",
        "review_granularity",
        "routing_strategy",
        "generation_mode",
        "countdown_seconds",
        "memory_mode",
        "budget_limit",
        "budget_spent",
        "budget_currency",
        "budget_warning_percent",
        "budget_pause_percent",
        "budget_paused",
        "config_json",
    ):
        if key in state_data:
            setattr(state, key, state_data[key])
    state.revision += 1
    volume_ids = db.scalars(
        select(models.Volume.id).where(models.Volume.project_id == project_id)
    ).all()
    db.execute(delete(models.Volume).where(models.Volume.id.in_(volume_ids or [-1])))
    db.execute(
        delete(models.CreativeArtifact).where(models.CreativeArtifact.project_id == project_id)
    )
    _restore_tree(db, project_id, cast(dict[str, Any], payload.get("tree") or {}))
    for item in cast(list[dict[str, Any]], payload.get("artifacts") or []):
        db.add(
            models.CreativeArtifact(
                project_id=project_id,
                kind=str(item.get("kind") or "note"),
                title=str(item.get("title") or "未命名成果"),
                content=str(item.get("content") or ""),
                status=str(item.get("status") or "pending"),
                source=str(item.get("source") or "restore"),
                position=int(item.get("position") or 0),
                version_number=int(item.get("version_number") or 1),
                notes=str(item.get("notes") or ""),
                metadata_json=str(item.get("metadata_json") or "{}"),
            )
        )
    db.flush()
    return project_overview(db, project_id)


def _state(db: Session, project_id: int) -> models.StudioProjectState:
    state = db.scalar(
        select(models.StudioProjectState).where(
            models.StudioProjectState.project_id == project_id,
            models.StudioProjectState.deleted_at.is_(None),
        )
    )
    if state is None:
        state = models.StudioProjectState(project_id=project_id)
        db.add(state)
        db.flush()
    return state


def _stage_order(state: models.StudioProjectState) -> list[str]:
    return CONTINUATION_STAGE_ORDER if state.entry_mode == "continuation" else STAGE_ORDER


def _project(db: Session, project_id: int) -> models.Project:
    project = db.scalar(
        select(models.Project).where(
            models.Project.id == project_id,
            models.Project.deleted_at.is_(None),
        )
    )
    if project is None:
        raise NotFoundError("项目不存在")
    return project


def _artifact(db: Session, artifact_id: int) -> models.CreativeArtifact:
    artifact = db.scalar(
        select(models.CreativeArtifact).where(
            models.CreativeArtifact.id == artifact_id,
            models.CreativeArtifact.deleted_at.is_(None),
        )
    )
    if artifact is None:
        raise NotFoundError("创作成果不存在")
    return artifact


def _advance_stage(db: Session, artifact: models.CreativeArtifact) -> None:
    state = _state(db, artifact.project_id)
    phase = artifact.kind
    if phase == "revision_proposal":
        return
    if phase in PHASE_AGENTS and phase not in {"drafting", "review"}:
        if not _phase_complete(db, artifact.project_id, phase):
            return
        if phase == "chapters":
            _ensure_chapter_tree_from_plan(db, artifact.project_id)
        elif phase == "continuation_plan":
            _ensure_continuation_tree_from_plan(db, artifact.project_id)
    if phase == "scene_draft":
        chapter_id = int(_json_object(artifact.metadata_json).get("chapter_id") or 0)
        candidates = db.scalars(
            select(models.CreativeArtifact).where(
                models.CreativeArtifact.project_id == artifact.project_id,
                models.CreativeArtifact.kind == "scene_draft",
                models.CreativeArtifact.status.in_(["pending", "changes_requested"]),
                models.CreativeArtifact.deleted_at.is_(None),
            )
        ).all()
        remaining = sum(
            1
            for item in candidates
            if int(_json_object(item.metadata_json).get("chapter_id") or 0) == chapter_id
        )
        if remaining == 0:
            _maybe_finish_drafting(db, artifact.project_id)
        return
    if phase == "drafting":
        _maybe_finish_drafting(db, artifact.project_id)
        return
    order = _stage_order(state)
    if phase in order:
        index = order.index(phase)
        if index + 1 < len(order):
            state.stage = order[index + 1]
            state.revision += 1


def _apply_artifact(db: Session, artifact: models.CreativeArtifact) -> None:
    metadata = _json_object(artifact.metadata_json)
    if artifact.kind == "revision_proposal":
        chapter_id = int(metadata.get("chapter_id") or 0)
        chapter = db.get(models.Chapter, chapter_id)
        if chapter is None:
            return
        create_snapshot(
            db,
            artifact.project_id,
            SnapshotCreate(label="AI 修改正文前", reason=f"应用：{artifact.title}"),
        )
        chapter.content = artifact.content
        chapter.word_count = word_count(chapter.content)
        chapter.revision += 1
        _update_chapter_memory(db, artifact.project_id, chapter)
        db.flush()
        _maybe_special_snapshot(db, artifact.project_id, chapter)
    elif artifact.kind == "drafting":
        chapter_id = int(metadata.get("chapter_id") or 0)
        chapter = db.get(models.Chapter, chapter_id)
        if chapter is not None:
            create_snapshot(
                db,
                artifact.project_id,
                SnapshotCreate(label="AI 正文写入前", reason=f"应用：{artifact.title}"),
            )
            mode = str(metadata.get("mode") or "new")
            if mode == "continue" and chapter.content.strip():
                chapter.content = chapter.content.rstrip() + "\n\n" + artifact.content.lstrip()
            else:
                chapter.content = artifact.content
            chapter.word_count = word_count(chapter.content)
            chapter.revision += 1
            _update_chapter_memory(db, artifact.project_id, chapter)
            db.flush()
            _maybe_special_snapshot(db, artifact.project_id, chapter)
    elif artifact.kind == "scene_draft":
        scene = db.get(models.Scene, int(metadata.get("scene_id") or 0))
        chapter = db.get(models.Chapter, int(metadata.get("chapter_id") or 0))
        if scene is not None and chapter is not None:
            create_snapshot(
                db,
                artifact.project_id,
                SnapshotCreate(label="AI 场景写入前", reason=f"应用：{artifact.title}"),
            )
            scene.content = artifact.content
            scene.revision += 1
            db.flush()
            scene_contents = db.scalars(
                select(models.Scene.content)
                .where(
                    models.Scene.chapter_id == chapter.id,
                    models.Scene.deleted_at.is_(None),
                )
                .order_by(models.Scene.position)
            ).all()
            chapter.content = "\n\n".join(item for item in scene_contents if item)
            chapter.word_count = word_count(chapter.content)
            chapter.revision += 1
            _update_chapter_memory(db, artifact.project_id, chapter)
            db.flush()
            _maybe_special_snapshot(db, artifact.project_id, chapter)
    elif artifact.kind == "chapters" and artifact.source == "import":
        _ensure_chapter_tree_from_plan(db, artifact.project_id)


def _update_chapter_memory(db: Session, project_id: int, chapter: models.Chapter) -> None:
    summary = db.scalar(
        select(models.ChapterSummary).where(models.ChapterSummary.chapter_id == chapter.id)
    )
    content = re.sub(r"\s+", " ", chapter.content).strip()
    short = content[:800] + ("…" if len(content) > 800 else "")
    if summary is None:
        db.add(
            models.ChapterSummary(
                chapter_id=chapter.id,
                summary=short,
                source="approved_chapter",
                token_count=max(1, len(short) // 2),
            )
        )
    else:
        summary.summary = short
        summary.source = "approved_chapter"
        summary.token_count = max(1, len(short) // 2)
        summary.revision += 1


def _maybe_special_snapshot(db: Session, project_id: int, chapter: models.Chapter) -> None:
    markers = ("真相", "死亡", "牺牲", "背叛", "决战", "身份揭晓", "重大转折", "再也无法")
    hit = next((marker for marker in markers if marker in chapter.content), None)
    if hit:
        create_snapshot(
            db,
            project_id,
            SnapshotCreate(
                label=f"剧情转折 · {chapter.title}",
                reason=f"AI 自动识别到重要转折信号：{hit}",
                special=True,
            ),
        )


def _refresh_continuation_conflict_pause(db: Session, project_id: int) -> None:
    state = _state(db, project_id)
    if state.entry_mode != "continuation":
        return
    candidates = db.scalars(
        select(models.CreativeArtifact).where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.status.in_(["pending", "changes_requested"]),
            models.CreativeArtifact.deleted_at.is_(None),
        )
    ).all()
    paused = any(
        bool(_json_object(item.metadata_json).get("requires_author_decision"))
        for item in candidates
    )
    config = _json_object(state.config_json)
    if bool(config.get("conflict_paused")) != paused:
        config["conflict_paused"] = paused
        state.config_json = _dump(config)
        state.revision += 1
        db.flush()


def _snapshot_payload(db: Session, project_id: int) -> dict[str, Any]:
    overview = project_overview(db, project_id).model_dump(mode="json")
    return {
        "format": "novel-agent-studio-v2-snapshot",
        "version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": overview["project"],
        "state": overview["state"],
        "artifacts": overview["artifacts"],
        "tree": overview["tree"],
    }


def _count(db: Session, model: type[Any], project_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(model.id)).where(
                model.project_id == project_id,
                model.deleted_at.is_(None),
            )
        )
        or 0
    )


def _state_record(state: models.StudioProjectState) -> dict[str, Any]:
    result = _record(state)
    result["config"] = _json_object(state.config_json)
    result["stage_label"] = STAGE_LABELS.get(state.stage, state.stage)
    return result


def _artifact_record(artifact: models.CreativeArtifact) -> dict[str, Any]:
    result = _record(artifact)
    result["metadata"] = _json_object(artifact.metadata_json)
    return result


def _message_record(message: models.StudioMessage) -> dict[str, Any]:
    result = _record(message)
    result["proposal"] = (
        _json_object(message.proposal_json) if message.proposal_json != "null" else None
    )
    return result


def _snapshot_record(snapshot: models.ProjectSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "project_id": snapshot.project_id,
        "kind": snapshot.kind,
        "label": snapshot.label,
        "reason": snapshot.reason,
        "permanent": snapshot.permanent,
        "created_at": snapshot.created_at.isoformat(),
    }


def _require_revision(row: Any, expected: int) -> None:
    if row.revision != expected:
        raise ConflictError("内容已在其他位置更新，请刷新后重试")


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
