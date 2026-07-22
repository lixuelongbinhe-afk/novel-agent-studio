from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import models
from app.repositories import word_count
from app.schemas import ModelDebugRequest, NormalizedContentPart, NormalizedMessage
from app.schemas.context import ContextBuildRequest
from app.schemas.studio import (
    ArtifactDecision,
    ArtifactUpdate,
    ChapterTreeRepairRequest,
    ChatRequest,
    ContinuationImportRequest,
    ContinuationSettingsUpdate,
    GenerateRequest,
    OutlineImportRequest,
    ProviderSetup,
    SnapshotCreate,
    StudioProjectCreate,
    StudioStateUpdate,
)
from app.services import context_builder, generation_jobs, model_execution
from app.services.chapter_plans import (
    chapter_title_number as _chapter_title_number,
    clean_outline_label as _clean_outline_label,
    is_generic_volume_title as _is_generic_volume_title,
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

def _merge_parsed_volumes(volumes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, volume in enumerate(volumes):
        title = _clean_outline_label(str(volume.get("title") or f"第{index + 1}卷"))
        key = _volume_plan_key(title)
        if key not in merged:
            merged[key] = {**volume, "title": title, "chapters": list(volume.get("chapters") or [])}
            order.append(key)
            continue
        current = merged[key]
        if _is_generic_volume_title(str(current.get("title") or "")) and not _is_generic_volume_title(title):
            current["title"] = title
        current["chapters"].extend(cast(list[dict[str, Any]], volume.get("chapters") or []))
    return [merged[key] for key in order]


def create_project(db: Session, payload: StudioProjectCreate) -> dict[str, Any]:
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
) -> dict[str, Any]:
    if payload.source_project_id is not None:
        source_text, source_name = _source_project_manuscript(db, payload.source_project_id)
    else:
        source_text = str(payload.text or "").strip()
        source_name = payload.source_name.strip() or "粘贴正文"
    if not source_text:
        raise HTTPException(status_code=422, detail="导入正文不能为空")

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
        raise HTTPException(status_code=409, detail="该项目不是半成品续写项目")
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
        raise HTTPException(status_code=409, detail="所选项目没有可导入的正文")
    return text, source.title


def _create_imported_manuscript_tree(
    db: Session, project_id: int, parsed: dict[str, Any]
) -> None:
    for volume_position, volume_data in enumerate(parsed["volumes"], 1):
        volume = models.Volume(
            project_id=project_id,
            title=str(volume_data["title"]),
            position=volume_position,
        )
        db.add(volume)
        db.flush()
        for chapter_position, chapter_data in enumerate(volume_data["chapters"], 1):
            content = str(chapter_data.get("content") or "")
            chapter = models.Chapter(
                project_id=project_id,
                volume_id=volume.id,
                number=_chapter_title_number(str(chapter_data["title"])),
                title=str(chapter_data["title"]),
                content=content,
                position=chapter_position,
                word_count=word_count(content),
            )
            db.add(chapter)
            db.flush()
            db.add(
                models.ChapterVersion(
                    chapter_id=chapter.id,
                    title=chapter.title,
                    content=content,
                    word_count=chapter.word_count,
                    source="continuation_import",
                )
            )
            compact = re.sub(r"\s+", " ", content).strip()
            db.add(
                models.ChapterSummary(
                    chapter_id=chapter.id,
                    summary=(compact[:800] + ("…" if len(compact) > 800 else "")),
                    source="continuation_import",
                    token_count=max(1, min(len(compact), 800) // 2),
                )
            )


def dashboard(db: Session) -> list[dict[str, Any]]:
    projects = db.scalars(
        select(models.Project)
        .where(models.Project.deleted_at.is_(None))
        .order_by(models.Project.updated_at.desc(), models.Project.id.desc())
    ).all()
    result: list[dict[str, Any]] = []
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
            {
                "id": project.id,
                "title": project.title,
                "summary": project.summary,
                "stage": state.stage,
                "stage_label": STAGE_LABELS.get(state.stage, state.stage),
                "completed_words": completed_words,
                "target_words": project.target_words,
                "pending_reviews": pending,
                "updated_at": project.updated_at,
                "entry_mode": state.entry_mode,
            }
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


def project_overview(db: Session, project_id: int) -> dict[str, Any]:
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
        .where(models.ProjectSnapshot.project_id == project_id)
        .order_by(models.ProjectSnapshot.created_at.desc(), models.ProjectSnapshot.id.desc())
    ).all()
    library_counts = {
        "entities": _count(db, models.StoryEntity, project_id),
        "timeline": _count(db, models.TimelineEvent, project_id),
        "foreshadows": _count(db, models.Foreshadow, project_id),
        "style_guides": _count(db, models.StyleGuide, project_id),
    }
    return {
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
    }


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
        raise HTTPException(status_code=409, detail="原始导入副本为永久只读内容，不能修改")
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
    series_key = str(metadata.get("series_key") or f"legacy:{current.kind}:{current.title}")
    candidates = db.scalars(
        select(models.CreativeArtifact)
        .where(
            models.CreativeArtifact.project_id == current.project_id,
            models.CreativeArtifact.kind == current.kind,
        )
        .order_by(models.CreativeArtifact.version_number.desc(), models.CreativeArtifact.id.desc())
    ).all()
    return [
        _artifact_record(item)
        for item in candidates
        if str(_json_object(item.metadata_json).get("series_key") or f"legacy:{item.kind}:{item.title}")
        == series_key
    ]


def decide_artifact(
    db: Session, artifact_id: int, payload: ArtifactDecision
) -> dict[str, Any]:
    artifact = _artifact(db, artifact_id)
    _require_revision(artifact, payload.expected_revision)
    artifact.notes = payload.note
    metadata = _json_object(artifact.metadata_json)
    if payload.action == "approve" and metadata.get("conflict_level") == "major":
        if payload.conflict_resolution is None:
            raise HTTPException(status_code=409, detail="重大冲突必须由作者选择处理方式")
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
            raise HTTPException(status_code=409, detail="请先编辑合并内容并保存新版本，再选择手工合并")
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
) -> dict[str, Any]:
    if phase not in PHASE_AGENTS:
        raise HTTPException(status_code=422, detail="不支持的创作阶段")
    project = _project(db, project_id)
    state = _state(db, project_id)
    _require_generation_prerequisites(db, project_id, phase, payload)
    phase_agents = PHASE_AGENTS[phase]
    if payload.agent_name is not None:
        phase_agents = [item for item in phase_agents if item[0] == payload.agent_name]
        if not phase_agents:
            raise HTTPException(status_code=422, detail="该阶段不存在指定的 Agent")
    if state.budget_paused:
        raise HTTPException(status_code=409, detail="项目预算已暂停，请先在费用面板确认继续")
    profile, reason = _select_model(db, state, payload.use_demo_model)
    lease = generation_jobs.acquire(
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
    job = lease.job
    if lease.replayed:
        replay_artifacts = _generation_job_artifacts(db, job)
        artifact = replay_artifacts[0] if replay_artifacts else None
        return {
            "job": _record(job),
            "artifact": _artifact_record(artifact) if artifact is not None else None,
            "artifacts": [_artifact_record(item) for item in replay_artifacts],
            "idempotent_replay": True,
        }

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
        completed_calls = 0
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
                raise HTTPException(status_code=409, detail="场景级审核需要该章节先建立场景大纲")
            combined = "\n\n".join(outputs)
            previous_scene = ""
            for scene_index, scene in enumerate(scenes):
                scene_metadata = dict(metadata)
                scene_metadata.update({"scene_id": scene.id, "scene_index": scene_index, "series_key": f"scene:{scene.id}"})
                _supersede_series(db, project_id, str(scene_metadata["series_key"]))
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
        return {
            "job": _record(job),
            "artifact": _artifact_record(artifacts[0]),
            "artifacts": [_artifact_record(item) for item in artifacts],
            "idempotent_replay": False,
        }
    except asyncio.CancelledError:
        generation_jobs.fail(db, job.id, "生成任务已取消", cancelled=True)
        raise
    except HTTPException as exc:
        generation_jobs.fail(db, job.id, str(exc.detail))
        raise
    except Exception as exc:
        generation_jobs.fail(db, job.id, str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
        raise HTTPException(status_code=502, detail=response.error.message)
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
        raise HTTPException(status_code=404, detail="对话消息不存在")
    if message.proposal_status != "pending":
        raise HTTPException(status_code=409, detail="该修改提案已处理")
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
            raise HTTPException(status_code=404, detail="对话消息不存在")
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
            raise HTTPException(status_code=404, detail="目标章节不存在")
        chapter.content = str(proposal.get("content") or "")
        chapter.word_count = word_count(chapter.content)
        chapter.revision += 1
    else:
        artifact = db.get(models.CreativeArtifact, int(proposal.get("target_id") or 0))
        if artifact is None:
            raise HTTPException(status_code=404, detail="目标创作成果不存在")
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


def parse_outline(text: str, title: str = "导入大纲") -> dict[str, Any]:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    volumes: list[dict[str, Any]] = []
    current_volume: dict[str, Any] | None = None
    current_chapter: dict[str, Any] | None = None
    body: list[str] = []
    preamble: list[str] = []

    def ensure_volume() -> dict[str, Any]:
        nonlocal current_volume
        if current_volume is None:
            current_volume = {"title": "第一卷", "chapters": []}
            volumes.append(current_volume)
        return current_volume

    def flush_body() -> None:
        nonlocal body
        if current_chapter is not None and body:
            content = "\n".join(body).strip()
            if content:
                current_chapter["synopsis"] = content
        body = []

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            if body and body[-1] != "":
                body.append("")
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        level = len(heading.group(1)) if heading else 0
        label = _clean_outline_label(heading.group(2).strip() if heading else stripped)
        if heading and label in AGENT_HEADINGS:
            continue
        is_volume = _volume_title_number(label) is not None or (level == 1 and not volumes)
        is_chapter = _chapter_title_number(label) is not None or level == 2
        is_scene = bool(re.match(r"^(场景|scene)\s*[一二三四五六七八九十0-9]*", label, re.I)) or level >= 3
        if is_volume and not is_chapter:
            flush_body()
            current_volume = {"title": label, "chapters": []}
            volumes.append(current_volume)
            current_chapter = None
        elif is_chapter:
            flush_body()
            volume = ensure_volume()
            current_chapter = {"title": label, "synopsis": "", "scenes": []}
            volume["chapters"].append(current_chapter)
            if preamble:
                body.extend(preamble)
                preamble = []
        elif is_scene and current_chapter is not None:
            flush_body()
            current_chapter["scenes"].append({"title": label, "synopsis": ""})
        else:
            if current_chapter is None:
                if not volumes:
                    preamble.append(stripped)
                    continue
                volume = ensure_volume()
                current_chapter = {"title": "第一章", "synopsis": "", "scenes": []}
                volume["chapters"].append(current_chapter)
            body.append(stripped)
    flush_body()
    volumes = _merge_parsed_volumes([item for item in volumes if item["chapters"]])
    if not volumes:
        volumes = [{"title": "第一卷", "chapters": [{"title": "第一章", "synopsis": text.strip(), "scenes": []}]}]
    chapter_count = sum(len(item["chapters"]) for item in volumes)
    scene_count = sum(len(chapter["scenes"]) for item in volumes for chapter in item["chapters"])
    warnings: list[str] = []
    if chapter_count == 1:
        warnings.append("只识别到一个章节，请在确认导入前检查标题层级。")
    return {
        "title": title,
        "volumes": volumes,
        "volume_count": len(volumes),
        "chapter_count": chapter_count,
        "scene_count": scene_count,
        "warnings": warnings,
    }


def import_outline(
    db: Session, project_id: int, payload: OutlineImportRequest
) -> dict[str, Any]:
    project = _project(db, project_id)
    parsed = parse_outline(payload.text, project.title)
    create_snapshot(
        db,
        project_id,
        SnapshotCreate(label="导入大纲前", reason="确认导入结构化大纲"),
    )
    if payload.replace_existing:
        volume_ids = db.scalars(
            select(models.Volume.id).where(models.Volume.project_id == project_id)
        ).all()
        db.execute(delete(models.Volume).where(models.Volume.id.in_(volume_ids or [-1])))
    for volume_position, volume_data in enumerate(parsed["volumes"], 1):
        volume = models.Volume(
            project_id=project_id,
            title=str(volume_data["title"]),
            position=volume_position,
        )
        db.add(volume)
        db.flush()
        for chapter_position, chapter_data in enumerate(volume_data["chapters"], 1):
            chapter = models.Chapter(
                project_id=project_id,
                volume_id=volume.id,
                number=_chapter_title_number(str(chapter_data["title"])),
                title=str(chapter_data["title"]),
                content="",
                position=chapter_position,
                word_count=0,
            )
            db.add(chapter)
            db.flush()
            for scene_position, scene_data in enumerate(chapter_data["scenes"], 1):
                db.add(
                    models.Scene(
                        chapter_id=chapter.id,
                        title=str(scene_data["title"]),
                        synopsis=str(scene_data.get("synopsis") or ""),
                        position=scene_position,
                    )
                )
    db.add(
        models.CreativeArtifact(
            project_id=project_id,
            kind="chapters",
            title="已导入的卷章大纲",
            content=payload.text,
            status="approved",
            source="import",
            position=STAGE_ORDER.index("chapters"),
            metadata_json=_dump(parsed),
        )
    )
    state = _state(db, project_id)
    state.stage = "drafting"
    state.revision += 1
    db.flush()
    return parsed


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


def restore_snapshot(db: Session, project_id: int, snapshot_id: int) -> dict[str, Any]:
    snapshot = db.get(models.ProjectSnapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise HTTPException(status_code=404, detail="项目快照不存在")
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
        raise HTTPException(status_code=409, detail="Provider 名称已存在")
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
        context_window=128_000,
        enabled=True,
    )
    db.add(profile)
    db.flush()
    if payload.api_key:
        try:
            set_provider_secret(provider.id, payload.api_key)
        except Exception:
            db.rollback()
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
        raise HTTPException(status_code=404, detail="Provider 不存在")
    set_provider_secret(provider_id, api_key)
    profiles = db.scalars(
        select(models.ModelProfile).where(models.ModelProfile.provider_account_id == provider_id)
    ).all()
    return _provider_record(db, provider, profiles[0] if profiles else None)


def delete_studio_provider(db: Session, provider_id: int) -> None:
    provider = db.get(models.ProviderAccount, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider 不存在")
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
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _artifact(db: Session, artifact_id: int) -> models.CreativeArtifact:
    artifact = db.scalar(
        select(models.CreativeArtifact).where(
            models.CreativeArtifact.id == artifact_id,
            models.CreativeArtifact.deleted_at.is_(None),
        )
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="创作成果不存在")
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
        )
    ).all()
    for item in artifacts:
        if str(_json_object(item.metadata_json).get("series_key") or "") == series_key:
            item.status = "superseded"
            item.revision += 1


def _ensure_chapter_tree_from_plan(db: Session, project_id: int) -> None:
    state = _state(db, project_id)
    config = _json_object(state.config_json)
    requested_chapters = max(1, min(int(config.get("chapter_count") or 12), 10_000))
    approved = db.scalars(
        select(models.CreativeArtifact)
        .where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.kind == "chapters",
            models.CreativeArtifact.status == "approved",
            models.CreativeArtifact.deleted_at.is_(None),
        )
        .order_by(models.CreativeArtifact.position, models.CreativeArtifact.id)
    ).all()
    chapter_plans = [
        item
        for item in approved
        if str(_json_object(item.metadata_json).get("agent_name") or item.title)
        == "章节规划师"
    ]
    source = "\n\n".join(item.content for item in (chapter_plans or approved))
    parsed = parse_outline(source, _project(db, project_id).title)
    parsed["volumes"] = _normalize_generated_chapter_plan(
        parsed["volumes"], requested_chapters, _approved_volume_titles(db, project_id)
    )
    planned_numbers = [
        number
        for volume in parsed["volumes"]
        for chapter in volume["chapters"]
        for number in [_chapter_title_number(str(chapter.get("title") or ""))]
        if number is not None
    ]
    expected_numbers = list(range(1, requested_chapters + 1))
    if sorted(planned_numbers) != expected_numbers:
        missing = sorted(set(expected_numbers) - set(planned_numbers))
        raise HTTPException(
            status_code=409,
            detail="批准的章节规划不能建立完整卷章树；仍缺少第 "
            f"{_format_number_ranges(missing)} 章，请返回章节规划重新生成。",
        )
    scene_plans = [
        item
        for item in approved
        if str(_json_object(item.metadata_json).get("agent_name") or item.title)
        == "场景规划师"
    ]
    scenes_by_chapter: dict[int, list[dict[str, Any]]] = {}
    if scene_plans:
        scene_source = "\n\n".join(item.content for item in scene_plans)
        scene_outline = parse_outline(scene_source, _project(db, project_id).title)
        for scene_volume in scene_outline["volumes"]:
            for scene_chapter in scene_volume["chapters"]:
                number = _chapter_title_number(str(scene_chapter.get("title") or ""))
                if number is not None and scene_chapter.get("scenes"):
                    scenes_by_chapter[number] = cast(
                        list[dict[str, Any]], scene_chapter["scenes"]
                    )
    for planned_volume in parsed["volumes"]:
        for planned_chapter in planned_volume["chapters"]:
            number = _chapter_title_number(str(planned_chapter.get("title") or ""))
            planned_chapter["scenes"] = scenes_by_chapter.get(
                number or 0,
                _default_chapter_scenes(str(planned_chapter.get("synopsis") or "")),
            )
    _reconcile_chapter_tree(db, project_id, parsed["volumes"], requested_chapters)


def _reconcile_chapter_tree(
    db: Session,
    project_id: int,
    planned_volumes: list[dict[str, Any]],
    requested_chapters: int,
) -> None:
    existing_volumes = list(
        db.scalars(
            select(models.Volume)
            .where(
                models.Volume.project_id == project_id,
                models.Volume.deleted_at.is_(None),
            )
            .order_by(models.Volume.position, models.Volume.id)
        ).all()
    )
    volume_ids = [volume.id for volume in existing_volumes]
    existing_chapters = list(
        db.scalars(
            select(models.Chapter)
            .where(
                models.Chapter.volume_id.in_(volume_ids or [-1]),
                models.Chapter.deleted_at.is_(None),
            )
            .order_by(models.Chapter.volume_id, models.Chapter.position, models.Chapter.id)
        ).all()
    )
    chapters_by_number: dict[int, models.Chapter] = {}
    unplanned_with_prose: list[models.Chapter] = []
    for chapter in existing_chapters:
        number = _chapter_title_number(chapter.title)
        if number is None or not 1 <= number <= requested_chapters:
            if chapter.content.strip():
                unplanned_with_prose.append(chapter)
            continue
        if number in chapters_by_number:
            raise HTTPException(
                status_code=409,
                detail=f"现有卷章树包含重复的第 {number} 章，请先使用章节修复工具处理。",
            )
        chapters_by_number[number] = chapter
    if unplanned_with_prose:
        names = "、".join(chapter.title for chapter in unplanned_with_prose[:8])
        raise HTTPException(
            status_code=409,
            detail=f"现有正文中有不属于批准计划的章节：{names}。为避免丢失正文，系统已停止推进，请先手工确认。",
        )

    volumes_by_number = {
        number: volume
        for volume in existing_volumes
        for number in [_volume_title_number(volume.title)]
        if number is not None
    }
    used_volume_ids: set[int] = set()
    used_chapter_ids: set[int] = set()
    for index, volume in enumerate(existing_volumes, 1):
        volume.position = -10_000 - index
    for index, chapter in enumerate(existing_chapters, 1):
        chapter.position = -10_000 - index
    db.flush()

    for volume_position, volume_data in enumerate(planned_volumes, 1):
        volume_title = str(volume_data["title"])
        volume_number = _volume_title_number(volume_title)
        planned_volume = volumes_by_number.get(volume_number or -1)
        if planned_volume is None:
            planned_volume = models.Volume(
                project_id=project_id,
                title=volume_title,
                position=volume_position,
            )
            db.add(planned_volume)
            db.flush()
        else:
            planned_volume.title = volume_title
            planned_volume.position = volume_position
            planned_volume.revision += 1
        used_volume_ids.add(planned_volume.id)

        for chapter_position, chapter_data in enumerate(volume_data["chapters"], 1):
            chapter_title = str(chapter_data["title"])
            number = _chapter_title_number(chapter_title)
            if number is None:
                raise HTTPException(status_code=409, detail=f"章节标题缺少规范编号：{chapter_title}")
            planned_chapter = chapters_by_number.get(number)
            if planned_chapter is None:
                planned_chapter = models.Chapter(
                    project_id=project_id,
                    volume_id=planned_volume.id,
                    number=number,
                    title=chapter_title,
                    content="",
                    position=chapter_position,
                    word_count=0,
                )
                db.add(planned_chapter)
                db.flush()
            else:
                planned_chapter.project_id = project_id
                planned_chapter.number = number
                planned_chapter.volume_id = planned_volume.id
                if not planned_chapter.content.strip() or _is_placeholder_chapter_title(planned_chapter.title):
                    planned_chapter.title = chapter_title
                planned_chapter.position = chapter_position
                planned_chapter.revision += 1
            used_chapter_ids.add(planned_chapter.id)
            _reconcile_chapter_scenes(
                db,
                planned_chapter,
                cast(list[dict[str, Any]], chapter_data.get("scenes") or []),
            )

    now = datetime.now(timezone.utc)
    for chapter in existing_chapters:
        if chapter.id not in used_chapter_ids:
            chapter.deleted_at = now
            chapter.revision += 1
    for volume in existing_volumes:
        if volume.id not in used_volume_ids:
            volume.deleted_at = now
            volume.revision += 1
    db.flush()

    active_chapters = db.scalars(
        select(models.Chapter)
        .join(models.Volume, models.Volume.id == models.Chapter.volume_id)
        .where(
            models.Volume.project_id == project_id,
            models.Volume.deleted_at.is_(None),
            models.Chapter.deleted_at.is_(None),
        )
    ).all()
    active_numbers = sorted(
        number
        for chapter in active_chapters
        for number in [_chapter_title_number(chapter.title)]
        if number is not None
    )
    if active_numbers != list(range(1, requested_chapters + 1)):
        raise HTTPException(
            status_code=409,
            detail="卷章树校验失败，阶段未推进；请保留项目并运行章节结构修复。",
        )


def _reconcile_chapter_scenes(
    db: Session, chapter: models.Chapter, planned_scenes: list[dict[str, Any]]
) -> None:
    if not planned_scenes:
        return
    existing = list(
        db.scalars(
            select(models.Scene)
            .where(
                models.Scene.chapter_id == chapter.id,
                models.Scene.deleted_at.is_(None),
            )
            .order_by(models.Scene.position, models.Scene.id)
        ).all()
    )
    for index, scene in enumerate(existing, 1):
        scene.position = -10_000 - index
    db.flush()
    for position, scene_data in enumerate(planned_scenes, 1):
        if position <= len(existing):
            scene = existing[position - 1]
            scene.title = str(scene_data.get("title") or f"场景{position}")
            scene.synopsis = str(scene_data.get("synopsis") or "")
            scene.position = position
            scene.revision += 1
        else:
            db.add(
                models.Scene(
                    chapter_id=chapter.id,
                    title=str(scene_data.get("title") or f"场景{position}"),
                    synopsis=str(scene_data.get("synopsis") or ""),
                    position=position,
                )
            )
    for offset, scene in enumerate(existing[len(planned_scenes):], len(planned_scenes) + 1):
        scene.position = offset
    db.flush()


def _approved_volume_titles(db: Session, project_id: int) -> list[str]:
    artifacts = db.scalars(
        select(models.CreativeArtifact)
        .where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.kind == "volumes",
            models.CreativeArtifact.status == "approved",
            models.CreativeArtifact.deleted_at.is_(None),
        )
        .order_by(models.CreativeArtifact.position, models.CreativeArtifact.id)
    ).all()
    titles: dict[int, str] = {}
    for artifact in artifacts:
        for line in artifact.content.splitlines():
            heading = re.match(r"^#{1,6}\s+(.+)$", line.strip())
            if heading is None:
                continue
            title = _clean_outline_label(heading.group(1))
            number = _volume_title_number(title)
            if number is not None and number not in titles:
                titles[number] = title
    return [titles[number] for number in sorted(titles)]


def _ensure_continuation_tree_from_plan(db: Session, project_id: int) -> None:
    state = _state(db, project_id)
    config = _json_object(state.config_json)
    volumes = list(db.scalars(
        select(models.Volume)
        .where(models.Volume.project_id == project_id, models.Volume.deleted_at.is_(None))
        .order_by(models.Volume.position, models.Volume.id)
    ).all())
    if not volumes:
        raise HTTPException(status_code=409, detail="导入正文没有可用分卷")
    existing_chapters = db.scalars(
        select(models.Chapter)
        .where(
            models.Chapter.volume_id.in_([volume.id for volume in volumes]),
            models.Chapter.deleted_at.is_(None),
        )
        .order_by(models.Chapter.position, models.Chapter.id)
    ).all()
    imported_count = int(config.get("imported_chapter_count") or len(existing_chapters))
    artifacts = db.scalars(
        select(models.CreativeArtifact)
        .where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.kind == "continuation_plan",
            models.CreativeArtifact.status == "approved",
            models.CreativeArtifact.deleted_at.is_(None),
        )
        .order_by(models.CreativeArtifact.position, models.CreativeArtifact.id)
    ).all()
    future_artifacts = [
        item
        for item in artifacts
        if str(_json_object(item.metadata_json).get("agent_name") or item.title)
        == "未来卷章规划"
    ]
    source = "\n\n".join(item.content for item in (future_artifacts or artifacts))
    parsed = parse_outline(source, _project(db, project_id).title)
    candidates = [
        chapter
        for volume in parsed["volumes"]
        for chapter in volume["chapters"]
        if (_chapter_title_number(str(chapter.get("title") or "")) or imported_count + 1)
        > imported_count
    ]
    configured_target = config.get("target_chapters")
    target_chapters = (
        max(imported_count, int(configured_target))
        if configured_target is not None
        else max(imported_count + len(candidates), imported_count + 12)
    )
    required = max(0, target_chapters - len(existing_chapters))
    desired_volumes = max(
        len(volumes), int(config.get("target_volumes") or len(parsed["volumes"]) or len(volumes))
    )
    while len(volumes) < desired_volumes:
        volume = models.Volume(
            project_id=project_id,
            title=f"第{len(volumes) + 1}卷 续篇",
            position=len(volumes) + 1,
        )
        db.add(volume)
        db.flush()
        volumes.append(volume)
    target_volume = volumes[-1]
    max_position = int(
        db.scalar(
            select(func.max(models.Chapter.position)).where(
                models.Chapter.volume_id == target_volume.id,
                models.Chapter.deleted_at.is_(None),
            )
        )
        or 0
    )
    for offset in range(required):
        number = len(existing_chapters) + offset + 1
        planned = candidates[offset] if offset < len(candidates) else {}
        title = str(planned.get("title") or f"第{number}章")
        if _chapter_title_number(title) is None:
            title = f"第{number}章 {title}"
        chapter = models.Chapter(
            project_id=project_id,
            volume_id=target_volume.id,
            number=number,
            title=title,
            content="",
            position=max_position + offset + 1,
            word_count=0,
        )
        db.add(chapter)
        db.flush()
        scenes = planned.get("scenes") or _default_chapter_scenes(
            str(planned.get("synopsis") or "")
        )
        for scene_position, scene in enumerate(scenes, 1):
            db.add(
                models.Scene(
                    chapter_id=chapter.id,
                    title=str(scene.get("title") or f"场景{scene_position}"),
                    synopsis=str(scene.get("synopsis") or ""),
                    position=scene_position,
                )
            )
    config["target_chapters"] = target_chapters
    config["target_volumes"] = desired_volumes
    config["plan_confirmed"] = True
    state.config_json = _dump(config)
    state.revision += 1
    db.flush()


def _chapter_generation_ranges(
    requested_chapters: int, batch_size: int = 10
) -> list[tuple[int, int]]:
    return [
        (start, min(start + batch_size - 1, requested_chapters))
        for start in range(1, requested_chapters + 1, batch_size)
    ]


def _chapter_plan_excerpt(text: str, start: int, end: int) -> str:
    lines = text.splitlines()
    selected: list[str] = []
    include = False
    for line in lines:
        heading = re.match(r"^##\s+(.+)$", line.strip())
        if heading:
            number = _chapter_title_number(heading.group(1).strip())
            include = number is not None and start <= number <= end
        if include:
            selected.append(line)
    return "\n".join(selected)


def _default_chapter_scenes(synopsis: str = "") -> list[dict[str, str]]:
    return [
        {"title": "场景一 起势", "synopsis": synopsis or "建立本章目标与即时阻力。"},
        {"title": "场景二 对抗", "synopsis": "推进冲突并揭示新信息。"},
        {"title": "场景三 转折", "synopsis": "形成变化并留下后续钩子。"},
    ]


def _normalize_generated_chapter_plan(
    volumes: list[dict[str, Any]],
    requested_chapters: int,
    approved_volume_titles: list[str] | None = None,
    *,
    fill_missing: bool = False,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, volume in enumerate(volumes):
        title = _clean_outline_label(str(volume.get("title") or f"第{index + 1}卷"))
        key = _volume_plan_key(title)
        if key not in merged:
            merged[key] = {**volume, "title": title, "chapters": []}
            order.append(key)
        elif _is_generic_volume_title(str(merged[key].get("title") or "")) and not _is_generic_volume_title(title):
            merged[key]["title"] = title
        merged[key]["chapters"].extend(
            cast(list[dict[str, Any]], volume.get("chapters") or [])
        )

    normalized: list[dict[str, Any]] = []
    used_numbers: set[int] = set()
    for key in order:
        volume = merged[key]
        chapters: list[dict[str, Any]] = []
        for chapter in cast(list[dict[str, Any]], volume.get("chapters") or []):
            title = str(chapter.get("title") or "").strip()
            number = _chapter_title_number(title)
            if number is None or number in used_numbers or number > requested_chapters:
                continue
            used_numbers.add(number)
            chapters.append(chapter)
        if chapters:
            normalized.append({"title": str(volume.get("title") or "第一卷"), "chapters": chapters})
    if not normalized:
        normalized = [{"title": "第一卷", "chapters": []}]

    normalized.sort(key=_planned_volume_sort_key)
    approved = _normalized_approved_volume_titles(
        approved_volume_titles or [], requested_chapters
    )
    if approved:
        approved_numbers = [_volume_title_number(title) for title in approved]
        index_by_number = {
            number: index
            for index, number in enumerate(approved_numbers)
            if number is not None
        }
        known_starts: dict[int, int] = {}
        existing_chapters: list[dict[str, Any]] = []
        for volume in normalized:
            chapters = cast(list[dict[str, Any]], volume.get("chapters") or [])
            existing_chapters.extend(chapters)
            volume_number = _volume_title_number(str(volume.get("title") or ""))
            chapter_numbers = [
                number
                for chapter in chapters
                for number in [_chapter_title_number(str(chapter.get("title") or ""))]
                if number is not None
            ]
            if volume_number in index_by_number and chapter_numbers:
                known_starts[index_by_number[volume_number]] = min(chapter_numbers)
        starts = _interpolate_volume_starts(
            len(approved), requested_chapters, known_starts
        )
        normalized = [{"title": title, "chapters": []} for title in approved]
        for chapter in existing_chapters:
            number = _chapter_title_number(str(chapter.get("title") or ""))
            if number is not None:
                normalized[_volume_index_for_chapter(number, starts)]["chapters"].append(
                    chapter
                )
    else:
        starts = [
            min(
                (
                    _chapter_title_number(str(chapter.get("title") or ""))
                    or requested_chapters + 1
                    for chapter in cast(list[dict[str, Any]], volume["chapters"])
                ),
                default=1,
            )
            for volume in normalized
        ]
    if fill_missing:
        for number in range(1, requested_chapters + 1):
            if number in used_numbers:
                continue
            target_index = _volume_index_for_chapter(number, starts)
            normalized[target_index]["chapters"].append(
                {
                    "title": f"第{number}章 [待规划]",
                    "synopsis": "待作者确认或重新生成本章规划。",
                    "scenes": [],
                    "planning_status": "missing",
                }
            )
            used_numbers.add(number)
    for volume in normalized:
        volume["chapters"].sort(
            key=lambda chapter: _chapter_title_number(str(chapter.get("title") or ""))
            or requested_chapters + 1
        )
    return normalized


def _chapter_plan_validation(
    content: str,
    requested_chapters: int,
    approved_volume_titles: list[str] | None = None,
) -> dict[str, Any]:
    raw_numbers = [
        number
        for line in content.splitlines()
        for number in [_chapter_title_number(_clean_outline_label(re.sub(r"^#{1,6}\s+", "", line.strip())))]
        if number is not None
    ]
    counts: dict[int, int] = {}
    for number in raw_numbers:
        counts[number] = counts.get(number, 0) + 1
    duplicates = sorted(number for number, count in counts.items() if count > 1)
    out_of_range = sorted(number for number in counts if number > requested_chapters)
    valid = {number for number in counts if 1 <= number <= requested_chapters}
    missing = sorted(set(range(1, requested_chapters + 1)) - valid)
    parsed = parse_outline(content, "章节规划")
    volumes = _normalize_generated_chapter_plan(
        parsed["volumes"], requested_chapters, approved_volume_titles
    )
    return {
        "requested_chapters": requested_chapters,
        "planned_chapters": len(valid),
        "coverage_percent": round((len(valid) / requested_chapters) * 100, 2),
        "missing_numbers": missing,
        "duplicate_numbers": duplicates,
        "out_of_range_numbers": out_of_range,
        "complete": not missing and not duplicates and not out_of_range,
        "volumes": volumes,
        "preview_markdown": _render_chapter_plan(volumes),
    }


def _render_chapter_plan(volumes: list[dict[str, Any]]) -> str:
    lines = ["## 章节规划师", "", "> 系统已规范化以下预览；审核通过后将按此结构写入卷章树。", ""]
    for volume in volumes:
        lines.append(f"# {volume['title']}")
        for chapter in cast(list[dict[str, Any]], volume.get("chapters") or []):
            lines.extend([f"## {chapter['title']}", str(chapter.get("synopsis") or "").strip(), ""])
    return "\n".join(lines).strip()


def _missing_chapter_numbers(content: str, start: int, end: int) -> list[int]:
    found = {
        number
        for line in content.splitlines()
        for number in [_chapter_title_number(_clean_outline_label(re.sub(r"^#{1,6}\s+", "", line.strip())))]
        if number is not None and start <= number <= end
    }
    return sorted(set(range(start, end + 1)) - found)


def _format_number_ranges(numbers: list[int]) -> str:
    if not numbers:
        return "无"
    ranges: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return "、".join(ranges)


def _validate_chapter_plan_approval(
    db: Session, artifact: models.CreativeArtifact
) -> None:
    metadata = _json_object(artifact.metadata_json)
    agent_name = str(metadata.get("agent_name") or artifact.title)
    if agent_name != "章节规划师":
        return
    state = _state(db, artifact.project_id)
    requested = max(
        1, min(int(_json_object(state.config_json).get("chapter_count") or 12), 10_000)
    )
    validation = _chapter_plan_validation(
        artifact.content, requested, _approved_volume_titles(db, artifact.project_id)
    )
    metadata["chapter_plan_validation"] = {
        key: value
        for key, value in validation.items()
        if key not in {"volumes", "preview_markdown"}
    }
    artifact.metadata_json = _dump(metadata)
    if not validation["complete"]:
        detail = []
        if validation["missing_numbers"]:
            detail.append(f"缺少第 {_format_number_ranges(validation['missing_numbers'])} 章")
        if validation["duplicate_numbers"]:
            detail.append(f"重复章节号：{_format_number_ranges(validation['duplicate_numbers'])}")
        if validation["out_of_range_numbers"]:
            detail.append(f"越界章节号：{_format_number_ranges(validation['out_of_range_numbers'])}")
        raise HTTPException(
            status_code=409,
            detail="章节规划尚不完整（覆盖率 "
            f"{validation['coverage_percent']}%）：{'；'.join(detail)}。请重新生成缺失章节后再审核。",
        )


def _normalized_approved_volume_titles(
    titles: list[str], requested_chapters: int
) -> list[str]:
    numbered: dict[int, str] = {}
    for title_value in titles:
        title = _clean_outline_label(title_value)
        number = _volume_title_number(title)
        if number is not None and number not in numbered:
            numbered[number] = title
    return [numbered[number] for number in sorted(numbered)][:requested_chapters]


def _interpolate_volume_starts(
    volume_count: int,
    requested_chapters: int,
    known_starts: dict[int, int],
) -> list[int]:
    if volume_count <= 0:
        return []
    anchors = {0: 1, volume_count: requested_chapters + 1}
    previous = 1
    for index, value in sorted(known_starts.items()):
        if 0 < index < volume_count and previous < value <= requested_chapters:
            anchors[index] = value
            previous = value
    starts = [1] * volume_count
    ordered = sorted(anchors.items())
    for (left_index, left_value), (right_index, right_value) in zip(
        ordered, ordered[1:]
    ):
        width = right_index - left_index
        for offset in range(width):
            starts[left_index + offset] = left_value + (
                (right_value - left_value) * offset // width
            )
    for index in range(1, len(starts)):
        starts[index] = max(starts[index], starts[index - 1] + 1)
    return starts


def _volume_plan_key(title: str) -> str:
    number = _volume_title_number(title)
    if number is not None:
        return f"number:{number}"
    return "title:" + re.sub(r"\s+", "", title).casefold()


def _planned_volume_sort_key(volume: dict[str, Any]) -> tuple[int, int]:
    title_number = _volume_title_number(str(volume.get("title") or ""))
    chapter_numbers = [
        number
        for chapter in cast(list[dict[str, Any]], volume.get("chapters") or [])
        for number in [_chapter_title_number(str(chapter.get("title") or ""))]
        if number is not None
    ]
    return (
        title_number if title_number is not None else 10_001,
        min(chapter_numbers) if chapter_numbers else 10_001,
    )


def _volume_index_for_chapter(number: int, starts: list[int]) -> int:
    target = 0
    for index, start in enumerate(starts):
        if number < start:
            break
        target = index
    return target


def chapter_tree_repair_preview(db: Session, project_id: int) -> dict[str, Any]:
    state = _state(db, project_id)
    requested_count = max(
        1, min(int(_json_object(state.config_json).get("chapter_count") or 12), 10_000)
    )
    volumes = db.scalars(
        select(models.Volume).where(
            models.Volume.project_id == project_id,
            models.Volume.deleted_at.is_(None),
        ).order_by(models.Volume.position, models.Volume.id)
    ).all()
    volume_ids = [volume.id for volume in volumes]
    volume_order = {volume.id: index for index, volume in enumerate(volumes)}
    chapters = db.scalars(
        select(models.Chapter)
        .where(
            models.Chapter.volume_id.in_(volume_ids or [-1]),
            models.Chapter.deleted_at.is_(None),
        )
        .order_by(models.Chapter.volume_id, models.Chapter.position, models.Chapter.id)
    ).all()
    chapters = sorted(
        chapters,
        key=lambda chapter: (
            volume_order.get(chapter.volume_id, len(volume_order)),
            chapter.position,
            chapter.id,
        ),
    )
    suspect_titles = {name for name, _ in PHASE_AGENTS["chapters"]}
    suspects = [item for item in chapters if item.title.strip() in suspect_titles]
    existing_numbers = {
        number
        for item in chapters
        if item not in suspects
        for number in [_chapter_title_number(item.title)]
        if number is not None
    }
    missing_numbers = [
        number for number in range(1, requested_count + 1) if number not in existing_numbers
    ]
    numbers_in_tree = [
        number
        for item in chapters
        if item not in suspects
        for number in [_chapter_title_number(item.title)]
        if number is not None
    ]
    out_of_order = numbers_in_tree != sorted(numbers_in_tree)
    duplicate_volumes: list[str] = []
    seen_volume_keys: set[str] = set()
    for volume in volumes:
        key = _volume_plan_key(volume.title)
        if key in seen_volume_keys:
            duplicate_volumes.append(volume.title)
        else:
            seen_volume_keys.add(key)
    position_errors = any(
        [chapter.position for chapter in chapters if chapter.volume_id == volume.id]
        != list(
            range(
                1,
                len([chapter for chapter in chapters if chapter.volume_id == volume.id]) + 1,
            )
        )
        for volume in volumes
    )
    return {
        "requested_count": requested_count,
        "active_count": len(chapters),
        "suspect_chapters": [
            {
                "id": item.id,
                "title": item.title,
                "word_count": item.word_count,
                "revision": item.revision,
            }
            for item in suspects
        ],
        "missing_numbers": missing_numbers,
        "out_of_order": out_of_order,
        "duplicate_volumes": duplicate_volumes,
        "position_errors": position_errors,
        "can_repair": bool(
            suspects
            or missing_numbers
            or out_of_order
            or duplicate_volumes
            or position_errors
        ),
    }


def repair_chapter_tree(
    db: Session,
    project_id: int,
    payload: ChapterTreeRepairRequest,
) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=409, detail="修复章节结构需要作者明确确认")
    preview = chapter_tree_repair_preview(db, project_id)
    if not preview["can_repair"]:
        return {"repaired": False, "overview": project_overview(db, project_id)}
    create_snapshot(
        db,
        project_id,
        SnapshotCreate(
            label="修复章节结构前",
            reason="合并重复分卷、恢复章节编号顺序并补齐缺号；原正文永久保留在此快照中",
            special=True,
        ),
    )
    now = datetime.now(timezone.utc)
    for suspect in cast(list[dict[str, Any]], preview["suspect_chapters"]):
        chapter = db.get(models.Chapter, int(suspect["id"]))
        if chapter is not None and chapter.deleted_at is None:
            chapter.deleted_at = now
            chapter.revision += 1
    volumes = db.scalars(
        select(models.Volume)
        .where(
            models.Volume.project_id == project_id,
            models.Volume.deleted_at.is_(None),
        )
        .order_by(models.Volume.position, models.Volume.id)
    ).all()
    if not volumes:
        raise HTTPException(status_code=409, detail="项目没有可用分卷，无法补齐章节")

    canonical_by_key: dict[str, models.Volume] = {}
    for volume in volumes:
        key = _volume_plan_key(volume.title)
        canonical = canonical_by_key.get(key)
        if canonical is None:
            canonical_by_key[key] = volume
            continue
        duplicate_chapters = db.scalars(
            select(models.Chapter).where(
                models.Chapter.volume_id == volume.id,
                models.Chapter.deleted_at.is_(None),
            )
        ).all()
        for chapter in duplicate_chapters:
            chapter.position = -30_000 - chapter.id
            chapter.volume_id = canonical.id
            chapter.revision += 1
        volume.deleted_at = now
        volume.revision += 1

    db.flush()
    volumes = [volume for volume in volumes if volume.deleted_at is None]
    active_chapters = list(
        db.scalars(
            select(models.Chapter).where(
                models.Chapter.volume_id.in_([volume.id for volume in volumes]),
                models.Chapter.deleted_at.is_(None),
            )
        ).all()
    )
    starts = _chapter_volume_starts(volumes, active_chapters)
    for number in cast(list[int], preview["missing_numbers"]):
        target_volume = volumes[_volume_index_for_chapter(number, starts)]
        chapter = models.Chapter(
            project_id=project_id,
            volume_id=target_volume.id,
            number=number,
            title=f"第{number}章",
            content="",
            position=-20_000 - number,
            word_count=0,
        )
        db.add(chapter)
        db.flush()
        for scene_position, scene in enumerate(_default_chapter_scenes(), 1):
            db.add(
                models.Scene(
                    chapter_id=chapter.id,
                    title=scene["title"],
                    synopsis=scene["synopsis"],
                    position=scene_position,
                )
            )
        active_chapters.append(chapter)

    numbered: dict[int, list[models.Chapter]] = {volume.id: [] for volume in volumes}
    unnumbered: dict[int, list[models.Chapter]] = {volume.id: [] for volume in volumes}
    for chapter in active_chapters:
        chapter_number = _chapter_title_number(chapter.title)
        if chapter_number is None:
            unnumbered.setdefault(chapter.volume_id, []).append(chapter)
            continue
        target_volume = volumes[_volume_index_for_chapter(chapter_number, starts)]
        if chapter.volume_id != target_volume.id:
            chapter.volume_id = target_volume.id
            chapter.revision += 1
        numbered[target_volume.id].append(chapter)

    for index, volume in enumerate(volumes, 1):
        volume.position = -40_000 - index
    for index, chapter in enumerate(active_chapters, 1):
        chapter.position = -50_000 - index
    db.flush()

    for volume_position, volume in enumerate(volumes, 1):
        if volume.position != volume_position:
            volume.position = volume_position
            volume.revision += 1
        ordered = sorted(
            numbered.get(volume.id, []),
            key=lambda chapter: (_chapter_title_number(chapter.title) or 10_001, chapter.id),
        ) + sorted(
            unnumbered.get(volume.id, []),
            key=lambda chapter: (chapter.position, chapter.id),
        )
        for position, chapter in enumerate(ordered, 1):
            if chapter.position != position:
                chapter.position = position
                chapter.revision += 1
    state = _state(db, project_id)
    if preview["missing_numbers"]:
        state.stage = "drafting"
        state.revision += 1
    db.flush()
    return {"repaired": True, "overview": project_overview(db, project_id)}


def _is_placeholder_chapter_title(title: str) -> bool:
    return bool(
        re.fullmatch(r"第\s*\d+\s*章", title.strip(), re.I)
        or re.fullmatch(r"chapter\s+\d+", title.strip(), re.I)
    )


def _chapter_volume_starts(
    volumes: list[models.Volume], chapters: list[models.Chapter]
) -> list[int]:
    starts: list[int] = []
    previous = 0
    for volume in volumes:
        volume_chapters = [item for item in chapters if item.volume_id == volume.id]
        named_numbers = [
            number
            for item in volume_chapters
            if not _is_placeholder_chapter_title(item.title)
            for number in [_chapter_title_number(item.title)]
            if number is not None
        ]
        all_numbers = [
            number
            for item in volume_chapters
            for number in [_chapter_title_number(item.title)]
            if number is not None
        ]
        candidate = min(named_numbers or all_numbers or [previous + 1])
        start = max(previous + 1, candidate)
        starts.append(start)
        previous = start
    if starts:
        starts[0] = min(starts[0], 1)
    return starts


def _require_generation_prerequisites(
    db: Session,
    project_id: int,
    phase: str,
    payload: GenerateRequest,
) -> None:
    state = _state(db, project_id)
    order = _stage_order(state)
    if phase not in order or state.stage not in order:
        raise HTTPException(status_code=409, detail="当前项目模式不支持该创作阶段")
    config = _json_object(state.config_json)
    if state.entry_mode == "continuation" and config.get("conflict_paused"):
        raise HTTPException(status_code=409, detail="发现重大连续性冲突，必须由作者确认处理后才能继续")
    phase_index = order.index(phase)
    current_index = order.index(state.stage)
    if phase_index > current_index and not (state.stage == "idea" and phase == "world"):
        raise HTTPException(status_code=409, detail=f"请先完成并批准“{STAGE_LABELS[state.stage]}”阶段")
    if phase == "drafting":
        if not payload.chapter_id:
            raise HTTPException(status_code=422, detail="正文生成必须选择章节")
        if state.entry_mode == "continuation" and config.get("continuation_start") == "choose":
            raise HTTPException(status_code=409, detail="请先选择接着写当前章或从下一章开始")
        planning = (
            ["continuation_analysis", "continuation_outline", "continuation_plan"]
            if state.entry_mode == "continuation"
            else ["world", "characters", "plot", "volumes", "chapters"]
        )
        if state.entry_mode in {"creative", "continuation"} and not all(
            _phase_complete(db, project_id, item) for item in planning
        ):
            raise HTTPException(status_code=409, detail="所有规划成果分别批准后才能开始正文")
        pending_planning = int(db.scalar(select(func.count(models.CreativeArtifact.id)).where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.kind.in_(planning),
            models.CreativeArtifact.status.in_(["pending", "changes_requested"]),
        )) or 0)
        if pending_planning:
            raise HTTPException(status_code=409, detail="仍有规划成果待审核，不能开始正文")
    if phase == "review":
        volume_ids = select(models.Volume.id).where(models.Volume.project_id == project_id)
        empty_chapters = int(db.scalar(select(func.count(models.Chapter.id)).where(
            models.Chapter.volume_id.in_(volume_ids),
            models.Chapter.word_count == 0,
        )) or 0)
        if empty_chapters:
            raise HTTPException(status_code=409, detail="仍有章节未完成，不能开始全文审阅")


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
        raise HTTPException(status_code=409, detail="项目预算已暂停，请先在费用面板确认继续")
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
            raise HTTPException(status_code=502, detail=partial.error.message)
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
        raise HTTPException(status_code=502, detail=response.error.message)
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
        raise HTTPException(status_code=409, detail="尚未配置可用 API，请先前往“模型与 API”添加密钥")
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
    overview = project_overview(db, project_id)
    return {
        "format": "novel-agent-studio-v2-snapshot",
        "version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": overview["project"],
        "state": overview["state"],
        "artifacts": overview["artifacts"],
        "tree": overview["tree"],
    }


def _restore_tree(db: Session, project_id: int, tree: dict[str, Any]) -> None:
    old_to_new_volumes: dict[int, int] = {}
    old_to_new_chapters: dict[int, int] = {}
    for item in cast(list[dict[str, Any]], tree.get("volumes") or []):
        volume = models.Volume(
            project_id=project_id,
            title=str(item.get("title") or "未命名卷"),
            position=int(item.get("position") or 0),
        )
        db.add(volume)
        db.flush()
        old_to_new_volumes[int(item.get("id") or 0)] = volume.id
    for item in cast(list[dict[str, Any]], tree.get("chapters") or []):
        volume_id = old_to_new_volumes.get(int(item.get("volume_id") or 0))
        if volume_id is None:
            continue
        chapter = models.Chapter(
            project_id=project_id,
            volume_id=volume_id,
            number=_chapter_title_number(str(item.get("title") or "")),
            title=str(item.get("title") or "未命名章"),
            content=str(item.get("content") or ""),
            position=int(item.get("position") or 0),
            word_count=int(item.get("word_count") or 0),
        )
        db.add(chapter)
        db.flush()
        old_to_new_chapters[int(item.get("id") or 0)] = chapter.id
    for item in cast(list[dict[str, Any]], tree.get("scenes") or []):
        chapter_id = old_to_new_chapters.get(int(item.get("chapter_id") or 0))
        if chapter_id is None:
            continue
        db.add(
            models.Scene(
                chapter_id=chapter_id,
                title=str(item.get("title") or "未命名场景"),
                synopsis=str(item.get("synopsis") or ""),
                content=str(item.get("content") or ""),
                position=int(item.get("position") or 0),
            )
        )


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
        "created_at": snapshot.created_at,
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
        raise HTTPException(status_code=409, detail="内容已在其他位置更新，请刷新后重试")


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
