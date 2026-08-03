from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, load_only

from app import models
from app.services.errors import (
    BudgetPausedError,
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
    UnavailableError,
    UpstreamFailedError,
)
from app.repositories import word_count
from app.schemas import ModelDebugRequest, NormalizedContentPart, NormalizedMessage
from app.schemas.context import ContextBuildRequest
from app.schemas.studio import (
    ArtifactDecision,
    ArtifactUpdate,
    ChatRequest,
    ContinuationImportRequest,
    ContinuationSettingsUpdate,
    DashboardProjectRead,
    GenerateResult,
    GenerateRequest,
    ProviderSetup,
    ProjectOverviewRead,
    SnapshotCreate,
    StudioProjectCreate,
    StudioStateUpdate,
)
from app.services import chapter_tree as _chapter_tree
from app.services import context_builder, generation_jobs, model_execution
from app.services.chapter_plans import (
    chapter_title_number as _chapter_title_number,
    clean_outline_label as _clean_outline_label,
    volume_title_number as _volume_title_number,
)
from app.services.credential_store import (
    delete_provider_secret,
    has_provider_secret,
    set_provider_secret,
)
from app.services.usage_control import estimate_text_tokens


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
        "target_mode": "manual" if any(
            value is not None
            for value in (payload.target_words, payload.target_chapters, payload.target_volumes)
        ) else "ai",
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
    if any(key in payload.model_fields_set for key in ("target_words", "target_chapters", "target_volumes")):
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
    volumes = _merge_parsed_volumes(
        [volume for volume in volumes if volume["chapters"]]
    )
    if not volumes:
        volumes = [{"title": "第一卷", "chapters": [{"title": "第一章", "content": normalized}]}]
    chapter_count = sum(len(volume["chapters"]) for volume in volumes)
    return {
        "title": title,
        "volumes": volumes,
        "volume_count": len(volumes),
        "chapter_count": chapter_count,
        "word_count": word_count(normalized),
        "warnings": ["只识别到一个章节，请确认原文中的章节标题格式。"] if chapter_count == 1 else [],
    }


def _source_project_manuscript(db: Session, project_id: int) -> tuple[str, str]:
    source = _project(db, project_id)
    volumes = list(db.scalars(
        select(models.Volume)
        .where(models.Volume.project_id == project_id, models.Volume.deleted_at.is_(None))
        .order_by(models.Volume.position, models.Volume.id)
    ).all())
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
    return ProjectOverviewRead.model_validate({
        "project": _record(project),
        "state": _state_record(state),
        "stages": [
            {"key": key, "label": STAGE_LABELS[key]}
            for key in _stage_order(state)
        ],
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
    })


def update_state(
    db: Session, project_id: int, payload: StudioStateUpdate
) -> dict[str, Any]:
    state = _state(db, project_id)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(state, key, value)
    state.revision += 1
    db.flush()
    return _state_record(state)


def update_artifact(
    db: Session, artifact_id: int, payload: ArtifactUpdate
) -> dict[str, Any]:
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
    if replacement.kind == "chapters" and str(
        replacement_metadata.get("agent_name") or replacement.title
    ) == "章节规划师":
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
    series_expression = func.json_extract(
        models.CreativeArtifact.metadata_json, "$.series_key"
    )
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


def decide_artifact(
    db: Session, artifact_id: int, payload: ArtifactDecision
) -> dict[str, Any]:
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


async def generate(
    db: Session, project_id: int, phase: str, payload: GenerateRequest
) -> GenerateResult:
    if phase not in PHASE_AGENTS:
        raise InvalidInputError("不支持的创作阶段")
    project = _project(db, project_id)
    state = _state(db, project_id)
    _require_generation_prerequisites(db, project_id, phase, payload)
    phase_agents = PHASE_AGENTS[phase]
    if payload.agent_name is not None:
        phase_agents = [item for item in phase_agents if item[0] == payload.agent_name]
        if not phase_agents:
            raise InvalidInputError("该阶段不存在指定的 Agent")
    if state.budget_paused:
        raise BudgetPausedError("项目预算已暂停，请先在费用面板确认继续")
    profile, reason = _select_model(db, state, payload.use_demo_model)
    completed_calls = 0
    total_calls = 0
    try:
        lease = await generation_jobs.acquire_async(
            db,
            project_id=project_id,
            phase=phase,
            chapter_id=payload.chapter_id,
            mode=payload.mode,
            idempotency_key=payload.idempotency_key,
            label=f"{STAGE_LABELS.get(phase, phase)} · {len(phase_agents)} 个 Agent",
            model_name=profile.display_name if profile is not None else "内置演示模型",
            model_reason=reason,
        )
    except generation_jobs.GenerationLeaseConflict as exc:
        raise UnavailableError("生成任务租约暂时不可用，请稍后重试") from exc
    job = lease.job
    if lease.replayed:
        replay_artifacts = _generation_job_artifacts(db, job)
        artifact = replay_artifacts[0] if replay_artifacts else None
        return GenerateResult.model_validate({
            "job": _record(job),
            "artifact": _artifact_record(artifact) if artifact is not None else None,
            "artifacts": [_artifact_record(item) for item in replay_artifacts],
            "idempotent_replay": True,
        })

    try:
        phase_max_tokens = _phase_output_tokens(phase)
        context, context_metadata = _generation_context(
            db,
            project_id,
            payload.chapter_id,
            profile=profile,
            use_demo=payload.use_demo_model,
            max_tokens=phase_max_tokens,
            query=(
                f"{STAGE_LABELS.get(phase, phase)}；{payload.instruction or '按已审核资料执行'}"
            ),
        )
        if phase in {"continuation_analysis", "continuation_outline"}:
            mapped_context, mapped_metadata = await _continuation_source_context(
                db,
                project_id,
                phase,
                profile,
                use_demo=payload.use_demo_model,
                max_tokens=phase_max_tokens,
            )
            context = _fit_text_to_token_budget(
                f"{context}\n\n{mapped_context}",
                _studio_input_budget(profile, payload.use_demo_model, phase_max_tokens),
            )
            context_metadata.update(mapped_metadata)
        job.model_reason = f"{reason} {_context_reason(context_metadata)}"
        db.commit()
        outputs: list[str] = []
        requested_chapters = max(
            1,
            min(
                int(_json_object(state.config_json).get("chapter_count") or 12),
                10_000,
            ),
        )
        chapter_ranges = (
            _chapter_generation_ranges(requested_chapters)
            if phase == "chapters"
            else [(1, 1)]
        )
        total_calls = len(phase_agents) * len(chapter_ranges)
        for index, (agent_name, responsibility) in enumerate(phase_agents):
            agent_parts: list[str] = []
            for range_start, range_end in chapter_ranges:
                batch_payload = payload
                collaborator_outputs = outputs
                if phase == "chapters":
                    batch_requirement = (
                        f"本次只规划第 {range_start} 至第 {range_end} 章，共 "
                        f"{range_end - range_start + 1} 章。必须逐章输出二级标题“## 第N章 标题”，"
                        "不得省略、合并或输出范围外章节。"
                    )
                    instruction = "\n".join(
                        item for item in [payload.instruction.strip(), batch_requirement] if item
                    )
                    batch_payload = payload.model_copy(update={"instruction": instruction})
                    collaborator_outputs = [
                        _chapter_plan_excerpt(item, range_start, range_end)
                        for item in outputs
                    ]
                prompt = _phase_prompt(
                    project,
                    phase,
                    agent_name,
                    responsibility,
                    context,
                    batch_payload,
                    collaborator_outputs,
                )
                response = await _model_call(
                    db,
                    project_id,
                    prompt,
                    profile,
                    use_demo=payload.use_demo_model,
                    max_tokens=phase_max_tokens,
                )
                if response.error is not None:
                    raise RuntimeError(f"{response.error.code}: {response.error.message}")
                response_text = response.text.strip()
                _record_response_cost(state, response)
                if phase == "chapters" and agent_name == "章节规划师":
                    missing = _missing_chapter_numbers(
                        response_text, range_start, range_end
                    )
                    if missing:
                        repair_prompt = (
                            f"{prompt}\n\n上一次输出缺少以下章节：{_format_number_ranges(missing)}。"
                            "请只补充这些缺失章节，每章必须使用二级标题“## 第N章 标题”，"
                            "不要重写已经生成的章节。"
                        )
                        repair_response = await _model_call(
                            db,
                            project_id,
                            repair_prompt,
                            profile,
                            use_demo=payload.use_demo_model,
                            max_tokens=phase_max_tokens,
                        )
                        if repair_response.error is not None:
                            raise RuntimeError(
                                f"{repair_response.error.code}: {repair_response.error.message}"
                            )
                        _record_response_cost(state, repair_response)
                        response_text = (
                            response_text + "\n\n" + repair_response.text.strip()
                        ).strip()
                agent_parts.append(response_text)
                completed_calls += 1
                job.progress = min(90, int((completed_calls / total_calls) * 85) + 5)
                db.commit()
            agent_output = f"## {agent_name}\n\n" + "\n\n".join(agent_parts)
            if phase == "chapters" and agent_name == "章节规划师":
                validation = _chapter_plan_validation(
                    agent_output,
                    requested_chapters,
                    _approved_volume_titles(db, project_id),
                )
                agent_output = str(validation["preview_markdown"])
            outputs.append(agent_output)
        metadata: dict[str, Any] = {
            "agents": [name for name, _ in phase_agents],
            "model": job.model_name,
            "model_reason": reason,
            "chapter_id": payload.chapter_id,
            "mode": payload.mode,
            "context": context_metadata,
            "generation_idempotency_key": payload.idempotency_key,
        }
        artifact_kind = phase
        if payload.mode not in {"new", "continue"}:
            artifact_kind = "revision_proposal"
            metadata["revision_mode"] = payload.mode
            metadata["selected_text"] = payload.selected_text
        artifacts: list[models.CreativeArtifact] = []
        if phase in {
            "world",
            "characters",
            "plot",
            "volumes",
            "chapters",
            "continuation_analysis",
            "continuation_outline",
            "continuation_plan",
        }:
            for agent_index, ((agent_name, _), output) in enumerate(
                zip(phase_agents, outputs, strict=True)
            ):
                item_metadata = dict(metadata)
                item_metadata.update({
                    "agent_name": agent_name,
                    "agent_index": agent_index,
                    "required_count": len(PHASE_AGENTS[phase]),
                    "series_key": f"{phase}:{agent_name}",
                })
                if phase == "chapters" and agent_name == "章节规划师":
                    validation = _chapter_plan_validation(
                        output,
                        requested_chapters,
                        _approved_volume_titles(db, project_id),
                    )
                    item_metadata["chapter_plan_validation"] = {
                        key: value
                        for key, value in validation.items()
                        if key not in {"volumes", "preview_markdown"}
                    }
                    item_metadata["normalized_preview"] = True
                _supersede_series(db, project_id, str(item_metadata["series_key"]))
                artifacts.append(_new_artifact(project_id, artifact_kind, agent_name, output, item_metadata, agent_index))
        elif phase == "drafting" and state.review_granularity == "scene":
            chapter = db.get(models.Chapter, int(payload.chapter_id or 0))
            scenes = db.scalars(
                select(models.Scene).where(
                    models.Scene.chapter_id == int(payload.chapter_id or 0),
                    models.Scene.deleted_at.is_(None),
                ).order_by(models.Scene.position)
            ).all()
            if chapter is None or not scenes:
                raise ConflictError("场景级审核需要该章节先建立场景大纲")
            combined = "\n\n".join(outputs)
            previous_scene = ""
            for scene_index, scene in enumerate(scenes):
                scene_metadata = dict(metadata)
                scene_metadata.update({"scene_id": scene.id, "scene_index": scene_index, "series_key": f"scene:{scene.id}"})
                scene_prompt = (
                    f"请只写小说《{project.title}》中“{chapter.title}”的场景正文。\n"
                    f"场景：{scene.title}\n场景要求：{scene.synopsis or '按章节大纲完成本场景。'}\n"
                    f"前一场景结尾：{previous_scene[-1200:] or '这是本章首场。'}\n\n"
                    f"项目上下文：\n{context}\n\n同章编辑建议：\n{combined}\n\n"
                    "输出可直接进入小说的正文，不要输出分析、标题或创作说明。"
                )
                scene_response = await _model_call(
                    db,
                    project_id,
                    scene_prompt,
                    profile,
                    use_demo=payload.use_demo_model,
                    max_tokens=3600,
                )
                if scene_response.error is not None:
                    raise RuntimeError(
                        f"{scene_response.error.code}: {scene_response.error.message}"
                    )
                _record_response_cost(state, scene_response)
                content = scene_response.text.strip()
                previous_scene = content
                artifacts.append(_new_artifact(project_id, "scene_draft", scene.title, content, scene_metadata, scene_index))
            for artifact in artifacts:
                _supersede_series(
                    db,
                    project_id,
                    str(_json_object(artifact.metadata_json)["series_key"]),
                )
        else:
            metadata["series_key"] = f"{artifact_kind}:{payload.chapter_id or 0}:{payload.mode}"
            _supersede_series(db, project_id, str(metadata["series_key"]))
            content = outputs[-1] if phase == "drafting" else "\n\n".join(outputs)
            artifacts.append(
                _new_artifact(
                    project_id,
                    artifact_kind,
                    _artifact_title(phase, payload),
                    content,
                    metadata,
                    0,
                )
            )
        for artifact in artifacts:
            _mark_conflicts(artifact)
            db.add(artifact)
        if state.entry_mode == "continuation" and any(
            _json_object(artifact.metadata_json).get("requires_author_decision")
            for artifact in artifacts
        ):
            state_config = _json_object(state.config_json)
            state_config["conflict_paused"] = True
            state.config_json = _dump(state_config)
            state.revision += 1
        db.flush()
        generation_jobs.complete(db, job, result_artifact_id=artifacts[0].id)
        _apply_budget_after_task(state)
        db.commit()
        return GenerateResult.model_validate({
            "job": _record(job),
            "artifact": _artifact_record(artifacts[0]),
            "artifacts": [_artifact_record(item) for item in artifacts],
            "idempotent_replay": False,
        })
    except asyncio.CancelledError:
        generation_jobs.fail(
            db,
            job.id,
            _generation_failure_message(
                "生成任务已取消", completed_calls, total_calls
            ),
            cancelled=True,
        )
        raise
    except DomainError as exc:
        generation_jobs.fail(
            db,
            job.id,
            _generation_failure_message(
                str(exc.detail), completed_calls, total_calls
            ),
        )
        raise
    except Exception as exc:
        message = _generation_failure_message(
            str(exc), completed_calls, total_calls
        )
        generation_jobs.fail(db, job.id, message)
        raise UpstreamFailedError(message) from exc


def _generation_job_artifacts(
    db: Session, job: models.GenerationJob
) -> list[models.CreativeArtifact]:
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
        if _json_object(item.metadata_json).get("generation_idempotency_key")
        == job.idempotency_key
    ]
    if matches:
        return matches
    fallback = db.get(models.CreativeArtifact, job.result_artifact_id or 0)
    return [fallback] if fallback is not None else []


def _generation_failure_message(reason: str, completed: int, total: int) -> str:
    return generation_jobs.failure_message(reason, completed, total)


async def chat(db: Session, project_id: int, payload: ChatRequest) -> dict[str, Any]:
    project = _project(db, project_id)
    state = _state(db, project_id)
    user_message = models.StudioMessage(
        project_id=project_id,
        role="user",
        content=payload.message,
        context_scope=_context_scope(payload),
    )
    db.add(user_message)
    db.commit()
    profile, reason = _select_model(db, state, payload.use_demo_model)
    context, context_metadata = _generation_context(
        db,
        project_id,
        payload.chapter_id,
        profile=profile,
        use_demo=payload.use_demo_model,
        max_tokens=2200,
        query=payload.message,
    )
    prompt = (
        "你是小说智能体工作室的总编助理。回答必须基于自动注入的项目上下文。"
        "若用户要求修改内容，先给出完整修改提案，不要假装已经写入。"
        "若用户要求推进、执行或进入下一步，只说明将创建待确认操作；"
        "在作者点击执行前，绝不能声称已经推进工作流或已经开始生成。\n\n"
        f"项目：{project.title}\n当前阶段：{STAGE_LABELS.get(state.stage, state.stage)}\n"
        f"自动上下文：\n{context}\n\n"
        f"当前选中文本：\n{payload.selected_text or '（无）'}\n\n"
        f"用户要求：{payload.message}"
    )
    response = await _model_call(db, project_id, prompt, profile, use_demo=payload.use_demo_model)
    if response.error is not None:
        raise UpstreamFailedError(response.error.message)
    proposal = _chat_proposal(db, project_id, payload, response.text)
    assistant = models.StudioMessage(
        project_id=project_id,
        role="assistant",
        content=response.text,
        context_scope=_context_scope(payload),
        proposal_json=_dump(proposal) if proposal is not None else "null",
        proposal_status="pending" if proposal is not None else "none",
        model_name=profile.display_name if profile is not None else "内置演示模型",
        model_reason=f"{reason} {_context_reason(context_metadata)}",
    )
    db.add(assistant)
    _record_response_cost(state, response)
    _apply_budget_after_task(state)
    db.commit()
    return _message_record(assistant)


async def decide_message_proposal(
    db: Session, project_id: int, message_id: int, action: str
) -> dict[str, Any]:
    message = db.get(models.StudioMessage, message_id)
    if message is None or message.project_id != project_id:
        raise NotFoundError("对话消息不存在")
    if message.proposal_status != "pending":
        raise ConflictError("该修改提案已处理")
    if action == "reject":
        message.proposal_status = "rejected"
        db.commit()
        return _message_record(message)
    proposal = _json_object(message.proposal_json)
    if proposal.get("target_type") == "workflow":
        phase = str(proposal.get("phase") or "")
        chapter_id = int(proposal.get("chapter_id") or 0) or None
        await generate(
            db,
            project_id,
            phase,
            GenerateRequest(
                idempotency_key=f"chat-proposal:{message.id}",
                chapter_id=chapter_id,
                use_demo_model=bool(proposal.get("use_demo_model")),
            ),
        )
        message = db.get(models.StudioMessage, message_id)
        if message is None:
            raise NotFoundError("对话消息不存在")
        message.proposal_status = "applied"
        db.commit()
        return _message_record(message)
    create_snapshot(
        db,
        project_id,
        SnapshotCreate(label="AI 对话修改前", reason="应用 AI 对话中的修改提案"),
    )
    if proposal.get("target_type") == "chapter":
        chapter = db.get(models.Chapter, int(proposal.get("target_id") or 0))
        if chapter is None:
            raise NotFoundError("目标章节不存在")
        chapter.content = str(proposal.get("content") or "")
        chapter.word_count = word_count(chapter.content)
        chapter.revision += 1
    else:
        artifact = db.get(models.CreativeArtifact, int(proposal.get("target_id") or 0))
        if artifact is None:
            raise NotFoundError("目标创作成果不存在")
        artifact.status = "superseded"
        artifact.revision += 1
        db.add(
            models.CreativeArtifact(
                project_id=project_id,
                kind=artifact.kind,
                title=artifact.title,
                content=str(proposal.get("content") or ""),
                status="pending",
                source="ai_chat",
                position=artifact.position,
                version_number=artifact.version_number + 1,
                metadata_json=artifact.metadata_json,
            )
        )
    message.proposal_status = "applied"
    db.commit()
    return _message_record(message)






def create_snapshot(
    db: Session, project_id: int, payload: SnapshotCreate
) -> dict[str, Any]:
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


def restore_snapshot(
    db: Session, project_id: int, snapshot_id: int
) -> ProjectOverviewRead:
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
    volume_ids = db.scalars(select(models.Volume.id).where(models.Volume.project_id == project_id)).all()
    db.execute(delete(models.Volume).where(models.Volume.id.in_(volume_ids or [-1])))
    db.execute(delete(models.CreativeArtifact).where(models.CreativeArtifact.project_id == project_id))
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


def setup_provider(db: Session, payload: ProviderSetup) -> dict[str, Any]:
    protocol_map = {
        "deepseek": "openai_chat",
        "openai": "openai_responses",
        "anthropic": "anthropic",
        "gemini": "gemini",
        "xai": "openai_chat",
        "openrouter": "openai_chat",
        "openai_compatible": "openai_chat",
    }
    if db.scalar(select(models.ProviderAccount).where(models.ProviderAccount.name == payload.name)):
        raise ConflictError("Provider 名称已存在")
    provider = models.ProviderAccount(
        name=payload.name,
        provider_type=protocol_map[payload.preset],
        credential_env_var=payload.env_var_name,
        base_url=payload.base_url.rstrip("/"),
        enabled=True,
    )
    db.add(provider)
    db.flush()
    db.add(
        models.ProtocolConfiguration(
            provider_account_id=provider.id,
            protocol=protocol_map[payload.preset],
            options_json="{}",
        )
    )
    profile = models.ModelProfile(
        provider_account_id=provider.id,
        name=payload.model,
        display_name=payload.model,
        context_window=1_000_000 if payload.preset == "deepseek" else 128_000,
        enabled=True,
    )
    db.add(profile)
    db.flush()
    if payload.api_key:
        try:
            set_provider_secret(provider.id, payload.api_key)
        except Exception:
            delete_provider_secret(provider.id)
            raise
    return _provider_record(db, provider, profile)


def list_studio_providers(db: Session) -> list[dict[str, Any]]:
    providers = db.scalars(
        select(models.ProviderAccount)
        .where(
            models.ProviderAccount.deleted_at.is_(None),
            models.ProviderAccount.provider_type.not_in(["ollama_native", "ollama"]),
        )
        .order_by(models.ProviderAccount.id)
    ).all()
    result: list[dict[str, Any]] = []
    for provider in providers:
        profiles = db.scalars(
            select(models.ModelProfile)
            .where(
                models.ModelProfile.provider_account_id == provider.id,
                models.ModelProfile.deleted_at.is_(None),
            )
            .order_by(models.ModelProfile.id)
        ).all()
        item = _provider_record(db, provider, profiles[0] if profiles else None)
        item["models"] = [_record(profile) for profile in profiles]
        result.append(item)
    return result


def update_provider_secret(db: Session, provider_id: int, api_key: str) -> dict[str, Any]:
    provider = db.get(models.ProviderAccount, provider_id)
    if provider is None or provider.deleted_at is not None:
        raise NotFoundError("Provider 不存在")
    set_provider_secret(provider_id, api_key)
    profiles = db.scalars(
        select(models.ModelProfile).where(models.ModelProfile.provider_account_id == provider_id)
    ).all()
    return _provider_record(db, provider, profiles[0] if profiles else None)


def delete_studio_provider(db: Session, provider_id: int) -> None:
    provider = db.get(models.ProviderAccount, provider_id)
    if provider is None:
        raise NotFoundError("Provider 不存在")
    delete_provider_secret(provider_id)
    provider.deleted_at = datetime.now(timezone.utc)
    provider.enabled = False
    provider.revision += 1
    db.flush()


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
        candidates = db.scalars(select(models.CreativeArtifact).where(
            models.CreativeArtifact.project_id == artifact.project_id,
            models.CreativeArtifact.kind == "scene_draft",
            models.CreativeArtifact.status.in_(["pending", "changes_requested"]),
            models.CreativeArtifact.deleted_at.is_(None),
        )).all()
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
                select(models.Scene.content).where(
                    models.Scene.chapter_id == chapter.id,
                    models.Scene.deleted_at.is_(None),
                ).order_by(models.Scene.position)
            ).all()
            chapter.content = "\n\n".join(item for item in scene_contents if item)
            chapter.word_count = word_count(chapter.content)
            chapter.revision += 1
            _update_chapter_memory(db, artifact.project_id, chapter)
            db.flush()
            _maybe_special_snapshot(db, artifact.project_id, chapter)
    elif artifact.kind == "chapters" and artifact.source == "import":
        _ensure_chapter_tree_from_plan(db, artifact.project_id)


def _update_chapter_memory(
    db: Session, project_id: int, chapter: models.Chapter
) -> None:
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


def _maybe_special_snapshot(
    db: Session, project_id: int, chapter: models.Chapter
) -> None:
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


def _new_artifact(
    project_id: int,
    kind: str,
    title: str,
    content: str,
    metadata: dict[str, Any],
    position_offset: int,
) -> models.CreativeArtifact:
    return models.CreativeArtifact(
        project_id=project_id,
        kind=kind,
        title=title,
        content=content,
        status="pending",
        source="ai",
        position=(STAGE_ORDER.index(kind) * 100 if kind in STAGE_ORDER else 700) + position_offset,
        metadata_json=_dump(metadata),
    )


def _mark_conflicts(artifact: models.CreativeArtifact) -> None:
    metadata = _json_object(artifact.metadata_json)
    major_markers = ("[重大冲突]", "【重大冲突】", "重大冲突：")
    minor_markers = ("[轻微冲突]", "【轻微冲突】", "轻微冲突：")
    if any(marker in artifact.content for marker in major_markers):
        metadata["conflict_level"] = "major"
        metadata["requires_author_decision"] = True
    elif any(marker in artifact.content for marker in minor_markers):
        metadata["conflict_level"] = "minor"
        metadata["minor_conflict_auto_fixed"] = True
        for marker in minor_markers:
            artifact.content = artifact.content.replace(marker, "[已自动校正的轻微冲突]")
        artifact.content += "\n\n> 系统标记：轻微冲突已按既有设定自动校正，请在审核时确认。"
    else:
        metadata["conflict_level"] = "none"
    artifact.metadata_json = _dump(metadata)


def _phase_complete(db: Session, project_id: int, phase: str) -> bool:
    required = {name for name, _ in PHASE_AGENTS.get(phase, [])}
    if not required:
        return True
    artifacts = db.scalars(
        select(models.CreativeArtifact).where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.kind == phase,
            models.CreativeArtifact.status != "superseded",
            models.CreativeArtifact.deleted_at.is_(None),
        )
    ).all()
    def handled(item: models.CreativeArtifact) -> bool:
        metadata = _json_object(item.metadata_json)
        return item.status == "approved" or (
            item.status == "rejected" and metadata.get("conflict_resolution") == "preserve_canon"
        )

    if not artifacts or any(not handled(item) for item in artifacts):
        return False
    approved = {
        str(_json_object(item.metadata_json).get("agent_name") or "")
        for item in artifacts
        if handled(item)
    }
    return required <= approved


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


def _supersede_series(db: Session, project_id: int, series_key: str) -> None:
    artifacts = db.scalars(
        select(models.CreativeArtifact).where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.status != "superseded",
            models.CreativeArtifact.deleted_at.is_(None),
            func.json_extract(
                models.CreativeArtifact.metadata_json, "$.series_key"
            )
            == series_key,
        )
    ).all()
    for item in artifacts:
        item.status = "superseded"
        item.revision += 1
















































def _require_generation_prerequisites(
    db: Session,
    project_id: int,
    phase: str,
    payload: GenerateRequest,
) -> None:
    state = _state(db, project_id)
    order = _stage_order(state)
    if phase not in order or state.stage not in order:
        raise ConflictError("当前项目模式不支持该创作阶段")
    config = _json_object(state.config_json)
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
        pending_planning = int(db.scalar(select(func.count(models.CreativeArtifact.id)).where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.kind.in_(planning),
            models.CreativeArtifact.status.in_(["pending", "changes_requested"]),
        )) or 0)
        if pending_planning:
            raise ConflictError("仍有规划成果待审核，不能开始正文")
    if phase == "review":
        volume_ids = select(models.Volume.id).where(models.Volume.project_id == project_id)
        empty_chapters = int(db.scalar(select(func.count(models.Chapter.id)).where(
            models.Chapter.volume_id.in_(volume_ids),
            models.Chapter.word_count == 0,
        )) or 0)
        if empty_chapters:
            raise ConflictError("仍有章节未完成，不能开始全文审阅")


def _maybe_finish_drafting(db: Session, project_id: int) -> None:
    volume_ids = select(models.Volume.id).where(models.Volume.project_id == project_id)
    empty = int(db.scalar(select(func.count(models.Chapter.id)).where(
        models.Chapter.volume_id.in_(volume_ids),
        models.Chapter.word_count == 0,
        models.Chapter.deleted_at.is_(None),
    )) or 0)
    if empty == 0:
        state = _state(db, project_id)
        state.stage = "review"
        state.revision += 1


async def extract_style_reference(
    db: Session,
    project_id: int,
    text: str,
    filename: str,
    use_demo_model: bool,
) -> dict[str, Any]:
    project = _project(db, project_id)
    state = _state(db, project_id)
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
            "\n\n".join(
                f"## 分片 {index + 1}\n{value}" for index, value in enumerate(partials)
            ),
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
    artifact = _new_artifact(project_id, "world", f"参考文风分析 · {filename}", response.text, metadata, 90)
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
        .join(models.ProviderAccount, models.ProviderAccount.id == models.ModelProfile.provider_account_id)
        .where(
            models.ModelProfile.enabled.is_(True),
            models.ModelProfile.deleted_at.is_(None),
            models.ProviderAccount.enabled.is_(True),
            models.ProviderAccount.deleted_at.is_(None),
            models.ProviderAccount.provider_type.not_in(["mock", "ollama", "ollama_native"]),
        )
    ).all()
    profiles = [profile for profile in profiles if _provider_has_key(db, profile.provider_account_id)]
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
            key=lambda item: (item.context_window / max(_price_score(db, item.id), 0.01))
            / max(_latency(db, item.provider_account_id), 100),
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
            compression_warnings.append(
                "Provider 返回上下文超限，已进一步压缩并自动重试。"
            )
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
        candidate = text[:head_count].rstrip() + marker + (
            text[-tail_count:].lstrip() if tail_count else ""
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
            + ("从当前章节最后一句自然接续，不要重写已有段落。\n" if payload.mode == "continue" else "\n")
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
        model_context_window=(
            8_192 if use_demo or profile is None else profile.context_window
        ),
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
    state = _state(db, project_id)
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
            summary_text = "\n".join(
                f"- {title}: {summary}" for title, summary in summaries
            )
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
        blocks.append("[人物与资料]\n" + "\n".join(f"- {item.name}: {item.description[:300]}" for item in entities))
    fallback = _fit_text_to_token_budget(
        "\n\n".join(blocks) or "尚无已批准资料。", context_budget
    )
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
        _record_response_cost(_state(db, project_id), response)
    aggregate = "\n\n".join(
        f"## 分片 {index + 1}\n{content}"
        for index, content in enumerate(map_outputs)
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


def _chat_proposal(
    db: Session, project_id: int, payload: ChatRequest, response_text: str
) -> dict[str, Any] | None:
    workflow_words = (
        "推进工作流",
        "推到工作流",
        "继续工作流",
        "进入下一阶段",
        "开始下一步",
        "执行下一步",
        "推吧",
    )
    if any(word in payload.message for word in workflow_words):
        return _workflow_chat_proposal(db, project_id, payload.use_demo_model)
    action_words = ("修改", "改写", "重写", "调整", "替换", "润色", "应用")
    if not any(word in payload.message for word in action_words):
        return None
    if payload.chapter_id:
        return {"target_type": "chapter", "target_id": payload.chapter_id, "content": response_text}
    state = _state(db, project_id)
    artifact = db.scalar(
        select(models.CreativeArtifact)
        .where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.kind == state.stage,
            models.CreativeArtifact.status.in_(["approved", "pending"]),
            models.CreativeArtifact.deleted_at.is_(None),
        )
        .order_by(models.CreativeArtifact.id.desc())
    )
    if artifact is None:
        return None
    return {"target_type": "artifact", "target_id": artifact.id, "content": response_text}


def _workflow_chat_proposal(
    db: Session, project_id: int, use_demo_model: bool
) -> dict[str, Any] | None:
    state = _state(db, project_id)
    phase = "world" if state.stage == "idea" else state.stage
    if phase not in PHASE_AGENTS:
        return None
    chapter_id: int | None = None
    label = f"生成{STAGE_LABELS.get(phase, phase)}"
    if phase == "drafting":
        chapter = db.scalar(
            select(models.Chapter)
            .join(models.Volume, models.Volume.id == models.Chapter.volume_id)
            .where(
                models.Volume.project_id == project_id,
                models.Volume.deleted_at.is_(None),
                models.Chapter.deleted_at.is_(None),
                models.Chapter.word_count == 0,
            )
            .order_by(models.Volume.position, models.Chapter.position, models.Chapter.id)
        )
        if chapter is None:
            return None
        chapter_id = chapter.id
        label = f"生成{chapter.title}正文"
        pending = db.scalars(
            select(models.CreativeArtifact).where(
                models.CreativeArtifact.project_id == project_id,
                models.CreativeArtifact.kind.in_(["drafting", "scene_draft"]),
                models.CreativeArtifact.status.in_(["pending", "changes_requested"]),
                models.CreativeArtifact.deleted_at.is_(None),
            )
        ).all()
        if any(
            int(_json_object(item.metadata_json).get("chapter_id") or 0) == chapter_id
            for item in pending
        ):
            return None
    proposal: dict[str, Any] = {
        "target_type": "workflow",
        "phase": phase,
        "label": label,
        "use_demo_model": use_demo_model,
    }
    if chapter_id is not None:
        proposal["chapter_id"] = chapter_id
    return proposal


def _context_scope(payload: ChatRequest) -> str:
    parts = ["project", payload.stage or "current_stage"]
    if payload.chapter_id:
        parts.append(f"chapter:{payload.chapter_id}")
    if payload.selected_text:
        parts.append("selection")
    return ",".join(parts)


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
    if state.budget_limit and state.budget_spent >= state.budget_limit * (state.budget_pause_percent / 100):
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




def _provider_record(
    db: Session,
    provider: models.ProviderAccount,
    profile: models.ModelProfile | None,
) -> dict[str, Any]:
    try:
        secret_stored = has_provider_secret(provider.id)
    except OSError:
        secret_stored = False
    return {
        "id": provider.id,
        "name": provider.name,
        "provider_type": provider.provider_type,
        "base_url": provider.base_url,
        "env_var_name": provider.credential_env_var,
        "secret_stored": secret_stored,
        "enabled": provider.enabled,
        "model": profile.name if profile else None,
        "revision": provider.revision,
    }


def _price_score(db: Session, model_id: int) -> float:
    pricing = db.scalar(
        select(models.ModelPricing)
        .where(
            models.ModelPricing.model_profile_id == model_id,
            models.ModelPricing.deleted_at.is_(None),
        )
        .order_by(models.ModelPricing.effective_from.desc())
    )
    if pricing is None:
        return 9999.0
    return float(pricing.input_per_million or 0) + float(pricing.output_per_million or 0)


def _latency(db: Session, provider_id: int) -> int:
    health = db.scalar(
        select(models.ProviderHealth).where(models.ProviderHealth.provider_account_id == provider_id)
    )
    return int(health.last_latency_ms or 9999) if health else 9999


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
    result["proposal"] = _json_object(message.proposal_json) if message.proposal_json != "null" else None
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


def _record(row: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in row.__table__.columns:
        if column.name == "payload_json":
            continue
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        result[column.name] = value
    return result


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
