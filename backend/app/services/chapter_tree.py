from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import models
from app.repositories import word_count
from app.schemas.studio import (
    ChapterTreeRepairRequest,
    OutlineImportRequest,
    ProjectOverviewRead,
    SnapshotCreate,
)
from app.services.chapter_plans import (
    chapter_title_number as _chapter_title_number,
    clean_outline_label as _clean_outline_label,
    is_generic_volume_title as _is_generic_volume_title,
    volume_title_number as _volume_title_number,
)
from app.services.errors import ConflictError, NotFoundError


CHAPTER_AGENT_NAMES = {"章节规划师", "场景规划师"}
AGENT_HEADINGS = CHAPTER_AGENT_NAMES
CHAPTER_STAGE_POSITION = 5


@dataclass(frozen=True)
class ChapterNode:
    number: int
    volume_index: int
    recycled: bool
    is_placeholder: bool
    has_manuscript: bool


@dataclass(frozen=True)
class ChapterTreeInvariants:
    """Constraints that every chapter-tree write must preserve."""

    total_chapters: int
    total_volumes: int

    def violations(self, chapters: list[ChapterNode]) -> list[str]:
        problems: list[str] = []
        active = [item for item in chapters if not item.recycled]
        numbers = [item.number for item in active]
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            problems.append(f"章号不连续：{_format_number_ranges(sorted(numbers))}")
        if len(numbers) != self.total_chapters:
            problems.append(
                f"章节数 {len(numbers)} 不等于项目设定 {self.total_chapters}"
            )
        if any(item.is_placeholder and item.has_manuscript for item in active):
            problems.append("占位章不得含正文")
        volume_indexes = {item.volume_index for item in active}
        if volume_indexes and max(volume_indexes) > self.total_volumes:
            problems.append(
                f"卷序号越界：{max(volume_indexes)} > {self.total_volumes}"
            )
        return problems


def _load_chapter_nodes(db: Session, project_id: int) -> list[ChapterNode]:
    volumes = list(
        db.scalars(
            select(models.Volume)
            .where(models.Volume.project_id == project_id)
            .order_by(models.Volume.position, models.Volume.id)
        ).all()
    )
    volume_indexes = {volume.id: volume.position for volume in volumes}
    chapters = db.scalars(
        select(models.Chapter).where(
            models.Chapter.project_id == project_id,
        )
    ).all()
    return [
        ChapterNode(
            number=(chapter.number or _chapter_title_number(chapter.title) or 0),
            volume_index=volume_indexes.get(chapter.volume_id, 0),
            recycled=(
                chapter.deleted_at is not None
                or chapter.volume_id not in volume_indexes
                or next(
                    (
                        volume.deleted_at is not None
                        for volume in volumes
                        if volume.id == chapter.volume_id
                    ),
                    True,
                )
            ),
            is_placeholder=(
                "[待规划]" in chapter.title
                or chapter.title.strip() in CHAPTER_AGENT_NAMES
            ),
            has_manuscript=bool(chapter.content.strip()),
        )
        for chapter in chapters
    ]


def _validate_tree(
    db: Session,
    project_id: int,
    *,
    total_chapters: int,
    total_volumes: int,
) -> None:
    invariants = ChapterTreeInvariants(
        total_chapters=total_chapters,
        total_volumes=total_volumes,
    )
    problems = invariants.violations(_load_chapter_nodes(db, project_id))
    if problems:
        raise ConflictError("章节结构与项目设定冲突：" + "；".join(problems))


def _state(db: Session, project_id: int) -> models.StudioProjectState:
    state = db.scalar(
        select(models.StudioProjectState).where(
            models.StudioProjectState.project_id == project_id
        )
    )
    if state is None:
        raise NotFoundError("项目创作状态不存在")
    return state


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


def _json_object(value: str | None) -> dict[str, Any]:
    parsed = json.loads(value or "{}")
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def create_snapshot(
    db: Session, project_id: int, payload: SnapshotCreate
) -> dict[str, Any]:
    from app.services import studio

    return studio.create_snapshot(db, project_id, payload)


def project_overview(db: Session, project_id: int) -> ProjectOverviewRead:
    from app.services import studio

    return studio.project_overview(db, project_id)


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
            position=CHAPTER_STAGE_POSITION,
            metadata_json=_dump(parsed),
        )
    )
    state = _state(db, project_id)
    state.stage = "drafting"
    state.revision += 1
    db.flush()
    return parsed

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
        raise ConflictError("批准的章节规划不能建立完整卷章树；仍缺少第 "
            f"{_format_number_ranges(missing)} 章，请返回章节规划重新生成。")
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
    _validate_tree(
        db,
        project_id,
        total_chapters=requested_chapters,
        total_volumes=len(parsed["volumes"]),
    )

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
            raise ConflictError(f"现有卷章树包含重复的第 {number} 章，请先使用章节修复工具处理。")
        chapters_by_number[number] = chapter
    if unplanned_with_prose:
        names = "、".join(chapter.title for chapter in unplanned_with_prose[:8])
        raise ConflictError(f"现有正文中有不属于批准计划的章节：{names}。为避免丢失正文，系统已停止推进，请先手工确认。")

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
                raise ConflictError(f"章节标题缺少规范编号：{chapter_title}")
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

    _validate_tree(
        db,
        project_id,
        total_chapters=requested_chapters,
        total_volumes=len(planned_volumes),
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
        raise ConflictError("导入正文没有可用分卷")
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
    _validate_tree(
        db,
        project_id,
        total_chapters=target_chapters,
        total_volumes=len(volumes),
    )

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
        raise ConflictError("章节规划尚不完整（覆盖率 "
            f"{validation['coverage_percent']}%）：{'；'.join(detail)}。请重新生成缺失章节后再审核。")

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
    suspect_titles = CHAPTER_AGENT_NAMES
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
        raise ConflictError("修复章节结构需要作者明确确认")
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
        raise ConflictError("项目没有可用分卷，无法补齐章节")

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
    _validate_tree(
        db,
        project_id,
        total_chapters=int(preview["requested_count"]),
        total_volumes=len(volumes),
    )
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
