from __future__ import annotations

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
from app.schemas.studio import (
    ArtifactDecision,
    ArtifactUpdate,
    ChatRequest,
    GenerateRequest,
    OutlineImportRequest,
    ProviderSetup,
    SnapshotCreate,
    StudioProjectCreate,
    StudioStateUpdate,
)
from app.services import model_execution
from app.services.credential_store import (
    delete_provider_secret,
    has_provider_secret,
    set_provider_secret,
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
}
PHASE_AGENTS: dict[str, list[tuple[str, str]]] = {
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
        "stages": [{"key": key, "label": STAGE_LABELS[key]} for key in STAGE_ORDER],
        "artifacts": [_artifact_record(item) for item in artifacts],
        "tree": {
            "volumes": [_record(item) for item in volumes],
            "chapters": [_record(item) for item in chapters],
            "scenes": [_record(item) for item in scenes],
        },
        "jobs": [_record(item) for item in jobs],
        "messages": [_message_record(item) for item in reversed(messages)],
        "snapshots": [_snapshot_record(item) for item in snapshots],
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
            return _artifact_record(artifact)
        if payload.conflict_resolution == "manual_merge" and artifact.source != "user":
            raise HTTPException(status_code=409, detail="请先编辑合并内容并保存新版本，再选择手工合并")
    if payload.action == "approve":
        artifact.status = "approved"
        _apply_artifact(db, artifact)
        _advance_stage(db, artifact)
    elif payload.action == "request_changes":
        artifact.status = "changes_requested"
    else:
        artifact.status = "rejected"
    artifact.revision += 1
    db.flush()
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
    job = models.GenerationJob(
        project_id=project_id,
        kind=phase,
        label=f"{STAGE_LABELS.get(phase, phase)} · {len(phase_agents)} 个 Agent",
        status="running",
        progress=5,
        model_name=profile.display_name if profile is not None else "内置演示模型",
        model_reason=reason,
    )
    db.add(job)
    db.commit()

    context = _generation_context(db, project_id, payload.chapter_id)
    outputs: list[str] = []
    try:
        for index, (agent_name, responsibility) in enumerate(phase_agents):
            prompt = _phase_prompt(
                project,
                phase,
                agent_name,
                responsibility,
                context,
                payload,
                outputs,
            )
            response = await _model_call(
                db,
                project_id,
                prompt,
                profile,
                use_demo=payload.use_demo_model,
                max_tokens=3600 if phase == "drafting" else 2200,
            )
            if response.error is not None:
                raise RuntimeError(f"{response.error.code}: {response.error.message}")
            outputs.append(f"## {agent_name}\n\n{response.text.strip()}")
            _record_response_cost(state, response)
            job.progress = min(90, int(((index + 1) / len(phase_agents)) * 85) + 5)
            db.commit()
        metadata: dict[str, Any] = {
            "agents": [name for name, _ in phase_agents],
            "model": job.model_name,
            "model_reason": reason,
            "chapter_id": payload.chapter_id,
            "mode": payload.mode,
        }
        artifact_kind = phase
        if payload.mode != "new":
            artifact_kind = "revision_proposal"
            metadata["revision_mode"] = payload.mode
            metadata["selected_text"] = payload.selected_text
        artifacts: list[models.CreativeArtifact] = []
        if phase in {"world", "characters", "plot", "volumes", "chapters"}:
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
            artifacts.append(_new_artifact(project_id, artifact_kind, _artifact_title(phase, payload), "\n\n".join(outputs), metadata, 0))
        for artifact in artifacts:
            _mark_conflicts(artifact)
            db.add(artifact)
        db.flush()
        job.result_artifact_id = artifacts[0].id
        job.status = "completed"
        job.progress = 100
        _apply_budget_after_task(state)
        db.commit()
        return {
            "job": _record(job),
            "artifact": _artifact_record(artifacts[0]),
            "artifacts": [_artifact_record(item) for item in artifacts],
        }
    except Exception as exc:
        db.rollback()
        failed_job = db.get(models.GenerationJob, job.id)
        if failed_job is not None:
            failed_job.status = "failed"
            failed_job.error_message = str(exc)[:2000]
            failed_job.progress = 100
            db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
    context = _generation_context(db, project_id, payload.chapter_id)
    prompt = (
        "你是小说智能体工作室的总编助理。回答必须基于自动注入的项目上下文。"
        "若用户要求修改内容，先给出完整修改提案，不要假装已经写入。\n\n"
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
        model_reason=reason,
    )
    db.add(assistant)
    _record_response_cost(state, response)
    _apply_budget_after_task(state)
    db.commit()
    return _message_record(assistant)


def decide_message_proposal(
    db: Session, project_id: int, message_id: int, action: str
) -> dict[str, Any]:
    message = db.get(models.StudioMessage, message_id)
    if message is None or message.project_id != project_id:
        raise HTTPException(status_code=404, detail="对话消息不存在")
    if message.proposal_status != "pending":
        raise HTTPException(status_code=409, detail="该修改提案已处理")
    if action == "reject":
        message.proposal_status = "rejected"
        db.flush()
        return _message_record(message)
    proposal = _json_object(message.proposal_json)
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
    db.flush()
    return _message_record(message)


def parse_outline(text: str, title: str = "导入大纲") -> dict[str, Any]:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    volumes: list[dict[str, Any]] = []
    current_volume: dict[str, Any] | None = None
    current_chapter: dict[str, Any] | None = None
    body: list[str] = []

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
        label = heading.group(2).strip() if heading else stripped
        is_volume = bool(re.match(r"^第.{1,12}卷(?:\s|[:：]|$)", label, re.I)) or (
            level == 1 and not volumes
        )
        is_chapter = bool(re.match(r"^第.{1,12}章(?:\s|[:：]|$)", label, re.I)) or bool(
            re.match(r"^chapter\s+\d+", label, re.I)
        ) or level == 2
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
        elif is_scene and current_chapter is not None:
            flush_body()
            current_chapter["scenes"].append({"title": label, "synopsis": ""})
        else:
            if current_chapter is None:
                volume = ensure_volume()
                current_chapter = {"title": "第一章", "synopsis": "", "scenes": []}
                volume["chapters"].append(current_chapter)
            body.append(stripped)
    flush_body()
    volumes = [item for item in volumes if item["chapters"]]
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
                volume_id=volume.id,
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
    if phase in STAGE_ORDER:
        index = STAGE_ORDER.index(phase)
        if index + 1 < len(STAGE_ORDER):
            state.stage = STAGE_ORDER[index + 1]
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
            chapter.content = artifact.content
            chapter.word_count = word_count(chapter.content)
            chapter.revision += 1
            _update_chapter_memory(db, artifact.project_id, chapter)
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
    if not artifacts or any(item.status != "approved" for item in artifacts):
        return False
    approved = {
        str(_json_object(item.metadata_json).get("agent_name") or "") for item in artifacts
    }
    return required <= approved


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
    existing = int(
        db.scalar(
            select(func.count(models.Volume.id)).where(
                models.Volume.project_id == project_id,
                models.Volume.deleted_at.is_(None),
            )
        )
        or 0
    )
    if existing:
        return
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
    source = "\n\n".join(item.content for item in approved)
    parsed = parse_outline(source, _project(db, project_id).title)
    parsed_chapters = [
        chapter
        for volume in parsed["volumes"]
        for chapter in volume["chapters"]
    ]
    recognized_chapters = sum(
        1
        for chapter in parsed_chapters
        if re.match(r"^(第.{1,12}章(?:\s|[:：]|$)|chapter\s+\d+)", str(chapter["title"]), re.I)
    )
    if recognized_chapters < 2:
        volume_count = max(1, min(8, (requested_chapters + 19) // 20))
        volumes: list[dict[str, Any]] = []
        chapter_number = 1
        for volume_number in range(1, volume_count + 1):
            remaining = requested_chapters - chapter_number + 1
            slots = (remaining + volume_count - volume_number) // (
                volume_count - volume_number + 1
            )
            chapters: list[dict[str, Any]] = []
            for _ in range(slots):
                chapters.append(
                    {
                        "title": f"第{chapter_number}章",
                        "synopsis": f"依据已批准的章节与场景规划完成第 {chapter_number} 章。",
                        "scenes": [
                            {"title": "场景一 起势", "synopsis": "建立本章目标与即时阻力。"},
                            {"title": "场景二 对抗", "synopsis": "推进冲突并揭示新信息。"},
                            {"title": "场景三 转折", "synopsis": "形成变化并留下后续钩子。"},
                        ],
                    }
                )
                chapter_number += 1
            volumes.append({"title": f"第{volume_number}卷", "chapters": chapters})
        parsed["volumes"] = volumes
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
                volume_id=volume.id,
                title=str(chapter_data["title"]),
                content="",
                position=chapter_position,
                word_count=0,
            )
            db.add(chapter)
            db.flush()
            scene_data = chapter_data.get("scenes") or [
                {"title": "场景一", "synopsis": str(chapter_data.get("synopsis") or "")}
            ]
            for scene_position, scene in enumerate(scene_data, 1):
                db.add(
                    models.Scene(
                        chapter_id=chapter.id,
                        title=str(scene["title"]),
                        synopsis=str(scene.get("synopsis") or ""),
                        position=scene_position,
                    )
                )


def _require_generation_prerequisites(
    db: Session,
    project_id: int,
    phase: str,
    payload: GenerateRequest,
) -> None:
    state = _state(db, project_id)
    phase_index = STAGE_ORDER.index(phase)
    current_index = STAGE_ORDER.index(state.stage)
    if phase_index > current_index and not (state.stage == "idea" and phase == "world"):
        raise HTTPException(status_code=409, detail=f"请先完成并批准“{STAGE_LABELS[state.stage]}”阶段")
    if phase == "drafting":
        if not payload.chapter_id:
            raise HTTPException(status_code=422, detail="正文生成必须选择章节")
        planning = ["world", "characters", "plot", "volumes", "chapters"]
        if state.entry_mode == "creative" and not all(_phase_complete(db, project_id, item) for item in planning):
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
    prompt = (
        "分析以下作者合法提供的参考文本，只提取可复用的抽象文风特征，不续写、不模仿具体句子。"
        "输出叙事视角、句式长度、节奏、描写密度、对白特点、常用意象、应避免事项和一份可执行文风规则。\n\n"
        f"项目：{project.title}\n文件：{filename}\n\n参考文本：\n{text}"
    )
    response = await _model_call(db, project_id, prompt, profile, use_demo=use_demo_model)
    if response.error is not None:
        raise HTTPException(status_code=502, detail=response.error.message)
    metadata = {
        "agent_name": "参考文风分析",
        "filename": filename,
        "model": profile.display_name if profile else "内置演示模型",
        "model_reason": reason,
        "series_key": "world:style-reference",
        "reference_characters": len(text),
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
    payload = ModelDebugRequest(
        model="mock-novel-v1" if use_demo or profile is None else profile.name,
        model_profile_id=None if use_demo or profile is None else profile.id,
        project_id=project_id,
        messages=[
            NormalizedMessage(
                role="user",
                content=[NormalizedContentPart(type="text", text=prompt)],
            )
        ],
        max_tokens=max_tokens,
        temperature=0.75,
        max_retries=5,
        allow_degradation=True,
    )
    return await model_execution.execute_model(db, payload)


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
    elif phase in {"world", "characters", "plot", "volumes"}:
        format_hint = "使用清晰的 Markdown 小节逐项输出，确保每项可以独立修改。\n"
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


def _generation_context(db: Session, project_id: int, chapter_id: int | None) -> str:
    artifacts = db.scalars(
        select(models.CreativeArtifact)
        .where(
            models.CreativeArtifact.project_id == project_id,
            models.CreativeArtifact.status == "approved",
            models.CreativeArtifact.deleted_at.is_(None),
        )
        .order_by(models.CreativeArtifact.position, models.CreativeArtifact.id.desc())
    ).all()
    blocks = [f"[{item.title}]\n{item.content[:5000]}" for item in artifacts[-8:]]
    if chapter_id:
        chapter = db.get(models.Chapter, chapter_id)
        if chapter is not None:
            blocks.append(f"[当前章节：{chapter.title}]\n{chapter.content[-8000:]}")
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
    return "\n\n".join(blocks)[:30_000] or "尚无已批准资料。"


def _chat_proposal(
    db: Session, project_id: int, payload: ChatRequest, response_text: str
) -> dict[str, Any] | None:
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
            volume_id=volume_id,
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
