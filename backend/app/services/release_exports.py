from __future__ import annotations

import csv
import io
import json
import platform
import re
import sys
import time
import zipfile
from xml.sax.saxutils import escape
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from app import models
from app.core.config import get_settings
from app.core.logging_config import list_log_files
from app.database import engine
from app.migrations import STUDIO_V2_REVISION
from app.schemas.release import ExportKind, ReleaseStatusRead
from app.services import agents, custom_adapters, workflows
from app.services.release_backup import current_table_counts


@dataclass(frozen=True)
class ExportArtifact:
    filename: str
    media_type: str
    content: bytes


def release_status(db: Session, *, frontend_bundled: bool) -> ReleaseStatusRead:
    settings = get_settings()
    integrity = _database_integrity(db)
    database_path = _database_path()
    return ReleaseStatusRead(
        app_version=settings.app_version,
        environment=settings.environment,
        migration_revision=STUDIO_V2_REVISION,
        frontend_bundled=frontend_bundled,
        database_integrity=integrity,
        database_bytes=(database_path.stat().st_size if database_path and database_path.exists() else 0),
        log_retention_days=settings.log_retention_days,
        log_files=len(list_log_files()),
        max_backup_bytes=settings.max_backup_bytes,
    )


def build_export(
    db: Session,
    kind: ExportKind,
    *,
    project_id: int | None = None,
    chapter_id: int | None = None,
    frontend_bundled: bool = False,
) -> ExportArtifact:
    if kind == "diagnostics_zip":
        return _diagnostics_export(db, frontend_bundled=frontend_bundled)
    if kind == "adapters_json":
        configs = db.scalars(
            select(models.GenericHttpAdapterConfiguration)
            .where(models.GenericHttpAdapterConfiguration.deleted_at.is_(None))
            .order_by(models.GenericHttpAdapterConfiguration.id)
        ).all()
        payload = {
            "format": "novel-agent-studio-adapters",
            "version": 1,
            "adapters": [
                custom_adapters.export_manifest(db, row.id).model_dump(mode="json")
                for row in configs
            ],
        }
        return ExportArtifact(
            "NovelAgentStudio-Adapter-Manifests.json",
            "application/json; charset=utf-8",
            _json_bytes(payload),
        )
    if project_id is None:
        raise ValueError("该导出类型必须选择项目")
    project = _project(db, project_id)
    stem = _safe_filename(project.title)
    if kind == "book_text":
        return ExportArtifact(
            f"{stem}-全书.txt",
            "text/plain; charset=utf-8",
            _book_text(db, project),
        )
    if kind == "book_markdown":
        return ExportArtifact(f"{stem}-全书.md", "text/markdown; charset=utf-8", _book_markdown(db, project))
    if kind == "book_pdf":
        return ExportArtifact(f"{stem}-全书.pdf", "application/pdf", _book_pdf(db, project))
    if kind == "chapter_markdown":
        if chapter_id is None:
            raise ValueError("单章导出必须选择章节")
        chapter = _chapter(db, project.id, chapter_id)
        content = f"# {chapter.title}\n\n{chapter.content.rstrip()}\n".encode("utf-8")
        return ExportArtifact(
            f"{stem}-{_safe_filename(chapter.title)}.md",
            "text/markdown; charset=utf-8",
            content,
        )
    if kind == "library_json":
        return ExportArtifact(
            f"{stem}-资料库.json",
            "application/json; charset=utf-8",
            _json_bytes(_library_payload(db, project)),
        )
    if kind == "timeline_csv":
        return ExportArtifact(
            f"{stem}-时间线.csv",
            "text/csv; charset=utf-8",
            _timeline_csv(db, project.id),
        )
    if kind == "foreshadows_json":
        rows = db.scalars(
            select(models.Foreshadow)
            .where(
                models.Foreshadow.project_id == project.id,
                models.Foreshadow.deleted_at.is_(None),
            )
            .order_by(models.Foreshadow.id)
        ).all()
        payload = {
            "format": "novel-agent-studio-foreshadows",
            "version": 1,
            "project": {"id": project.id, "title": project.title},
            "foreshadows": [_record(row) for row in rows],
        }
        return ExportArtifact(
            f"{stem}-伏笔.json", "application/json; charset=utf-8", _json_bytes(payload)
        )
    if kind == "agents_json":
        payload = {
            "format": "novel-agent-studio-agents",
            "version": 1,
            "project": {"id": project.id, "title": project.title},
            "agents": [item.model_dump(mode="json") for item in agents.list_agents(db, project.id)],
        }
        return ExportArtifact(
            f"{stem}-智能体.json", "application/json; charset=utf-8", _json_bytes(payload)
        )
    if kind == "workflows_json":
        workflow_rows = db.scalars(
            select(models.Workflow)
            .where(
                models.Workflow.project_id == project.id,
                models.Workflow.deleted_at.is_(None),
            )
            .order_by(models.Workflow.id)
        ).all()
        payload = {
            "format": "novel-agent-studio-workflows",
            "version": 1,
            "project": {"id": project.id, "title": project.title},
            "workflows": [
                workflows.export_manifest(db, row.id).model_dump(mode="json")
                for row in workflow_rows
            ],
        }
        return ExportArtifact(
            f"{stem}-工作流.json", "application/json; charset=utf-8", _json_bytes(payload)
        )
    raise ValueError(f"不支持的导出类型：{kind}")


def _book_markdown(db: Session, project: models.Project) -> bytes:
    volumes = db.scalars(
        select(models.Volume)
        .where(models.Volume.project_id == project.id, models.Volume.deleted_at.is_(None))
        .order_by(models.Volume.position, models.Volume.id)
    ).all()
    volume_ids = [row.id for row in volumes]
    chapters = db.scalars(
        select(models.Chapter)
        .where(
            models.Chapter.volume_id.in_(volume_ids or [-1]),
            models.Chapter.deleted_at.is_(None),
        )
        .order_by(models.Chapter.volume_id, models.Chapter.position, models.Chapter.id)
    ).all()
    by_volume: dict[int, list[models.Chapter]] = {}
    for chapter in chapters:
        by_volume.setdefault(chapter.volume_id, []).append(chapter)
    lines = [f"# {project.title}", ""]
    if project.summary.strip():
        lines.extend([project.summary.strip(), ""])
    for volume in volumes:
        lines.extend([f"## {volume.title}", ""])
        for chapter in by_volume.get(volume.id, []):
            lines.extend([f"### {chapter.title}", "", chapter.content.rstrip(), ""])
    return "\n".join(lines).rstrip().encode("utf-8") + b"\n"


def _book_text(db: Session, project: models.Project) -> bytes:
    volumes, by_volume = _book_rows(db, project.id)
    lines = [project.title, "=" * len(project.title), ""]
    if project.summary.strip():
        lines.extend([project.summary.strip(), ""])
    for volume in volumes:
        lines.extend([volume.title, ""])
        for chapter in by_volume.get(volume.id, []):
            lines.extend([chapter.title, "", chapter.content.rstrip(), ""])
    return b"\xef\xbb\xbf" + "\r\n".join(lines).rstrip().encode("utf-8") + b"\r\n"


def _book_pdf(db: Session, project: models.Project) -> bytes:
    volumes, by_volume = _book_rows(db, project.id)
    output = io.BytesIO()
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ChineseTitle",
        parent=styles["Title"],
        fontName="STSong-Light",
        fontSize=24,
        leading=34,
        alignment=TA_CENTER,
        spaceAfter=18,
    )
    volume_style = ParagraphStyle(
        "ChineseVolume",
        parent=styles["Heading1"],
        fontName="STSong-Light",
        fontSize=17,
        leading=25,
        spaceBefore=8,
        spaceAfter=12,
    )
    chapter_style = ParagraphStyle(
        "ChineseChapter",
        parent=styles["Heading2"],
        fontName="STSong-Light",
        fontSize=14,
        leading=22,
        spaceBefore=6,
        spaceAfter=10,
    )
    body = ParagraphStyle(
        "ChineseBody",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=11,
        leading=20,
        firstLineIndent=22,
        spaceAfter=5,
    )
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=24 * mm,
        rightMargin=24 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title=project.title,
        author="Novel Agent Studio",
    )
    story: list[Any] = [Paragraph(escape(project.title), title)]
    if project.summary.strip():
        story.extend([Paragraph(escape(project.summary.strip()), body), PageBreak()])
    for volume_index, volume in enumerate(volumes):
        if volume_index:
            story.append(PageBreak())
        story.append(Paragraph(escape(volume.title), volume_style))
        for chapter in by_volume.get(volume.id, []):
            story.append(Paragraph(escape(chapter.title), chapter_style))
            paragraphs = re.split(r"\n\s*\n", chapter.content.strip())
            for paragraph in paragraphs:
                if paragraph.strip():
                    story.append(Paragraph(escape(paragraph.strip()).replace("\n", "<br/>"), body))
            story.append(Spacer(1, 6 * mm))
    document.build(story)
    return output.getvalue()


def _book_rows(
    db: Session, project_id: int
) -> tuple[list[models.Volume], dict[int, list[models.Chapter]]]:
    volumes = list(
        db.scalars(
            select(models.Volume)
            .where(models.Volume.project_id == project_id, models.Volume.deleted_at.is_(None))
            .order_by(models.Volume.position, models.Volume.id)
        ).all()
    )
    volume_ids = [row.id for row in volumes]
    chapters = db.scalars(
        select(models.Chapter)
        .where(
            models.Chapter.volume_id.in_(volume_ids or [-1]),
            models.Chapter.deleted_at.is_(None),
        )
        .order_by(models.Chapter.volume_id, models.Chapter.position, models.Chapter.id)
    ).all()
    by_volume: dict[int, list[models.Chapter]] = {}
    for chapter in chapters:
        by_volume.setdefault(chapter.volume_id, []).append(chapter)
    return volumes, by_volume


def _library_payload(db: Session, project: models.Project) -> dict[str, Any]:
    entities = db.scalars(
        select(models.StoryEntity)
        .where(models.StoryEntity.project_id == project.id, models.StoryEntity.deleted_at.is_(None))
        .order_by(models.StoryEntity.id)
    ).all()
    entity_ids = [row.id for row in entities]
    aliases = db.scalars(
        select(models.EntityAlias)
        .where(
            models.EntityAlias.entity_id.in_(entity_ids or [-1]),
            models.EntityAlias.deleted_at.is_(None),
        )
        .order_by(models.EntityAlias.id)
    ).all()
    state_changes = db.scalars(
        select(models.EntityStateChange)
        .where(
            models.EntityStateChange.entity_id.in_(entity_ids or [-1]),
            models.EntityStateChange.deleted_at.is_(None),
        )
        .order_by(models.EntityStateChange.id)
    ).all()
    relations = db.scalars(
        select(models.EntityRelation)
        .where(
            models.EntityRelation.project_id == project.id,
            models.EntityRelation.deleted_at.is_(None),
        )
        .order_by(models.EntityRelation.id)
    ).all()
    guides = db.scalars(
        select(models.StyleGuide)
        .where(models.StyleGuide.project_id == project.id, models.StyleGuide.deleted_at.is_(None))
        .order_by(models.StyleGuide.id)
    ).all()
    return {
        "format": "novel-agent-studio-library",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "project": {"id": project.id, "title": project.title},
        "entities": [_record(row, json_fields={"tags"}) for row in entities],
        "aliases": [_record(row) for row in aliases],
        "relations": [_record(row) for row in relations],
        "state_changes": [_record(row) for row in state_changes],
        "style_guides": [_record(row) for row in guides],
    }


def _timeline_csv(db: Session, project_id: int) -> bytes:
    rows = db.scalars(
        select(models.TimelineEvent)
        .where(
            models.TimelineEvent.project_id == project_id,
            models.TimelineEvent.deleted_at.is_(None),
        )
        .order_by(models.TimelineEvent.position, models.TimelineEvent.id)
    ).all()
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(["ID", "事件时间", "标题", "说明", "章节 ID", "顺序"])
    for row in rows:
        writer.writerow(
            [row.id, row.event_time, row.label, row.description, row.chapter_id or "", row.position]
        )
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def _diagnostics_export(db: Session, *, frontend_bundled: bool) -> ExportArtifact:
    settings = get_settings()
    status = release_status(db, frontend_bundled=frontend_bundled)
    packages: dict[str, str] = {}
    for package in ("fastapi", "uvicorn", "pydantic", "sqlalchemy", "alembic", "httpx"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = "not-installed"
    counts = current_table_counts(db)
    database_path = _database_path()
    diagnostics = {
        "format": "novel-agent-studio-redacted-diagnostics",
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release": status.model_dump(mode="json"),
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable_packaged": bool(getattr(sys, "frozen", False)),
            "packages": packages,
        },
        "database": {
            "filename": database_path.name if database_path else "non-file-database",
            "tables": [item.model_dump() for item in counts],
        },
        "privacy": {
            "telemetry_enabled": False,
            "manuscript_content_included": False,
            "log_content_included": False,
            "environment_values_included": False,
            "credentials_included": False,
        },
        "limits": {
            "backup_compressed_bytes": settings.max_backup_bytes,
            "backup_uncompressed_bytes": settings.max_backup_uncompressed_bytes,
        },
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", _json_bytes(diagnostics))
        archive.writestr(
            "README.txt",
            "这是脱敏诊断包：不包含小说正文、日志内容、环境变量值或任何凭据。\n",
        )
    return ExportArtifact(
        "NovelAgentStudio-Redacted-Diagnostics.zip", "application/zip", output.getvalue()
    )


def _project(db: Session, project_id: int) -> models.Project:
    row = db.scalar(
        select(models.Project).where(
            models.Project.id == project_id, models.Project.deleted_at.is_(None)
        )
    )
    if row is None:
        raise ValueError("项目不存在或已删除")
    return row


def _chapter(db: Session, project_id: int, chapter_id: int) -> models.Chapter:
    row = db.scalar(
        select(models.Chapter)
        .join(models.Volume, models.Volume.id == models.Chapter.volume_id)
        .where(
            models.Chapter.id == chapter_id,
            models.Chapter.deleted_at.is_(None),
            models.Volume.project_id == project_id,
            models.Volume.deleted_at.is_(None),
        )
    )
    if row is None:
        raise ValueError("章节不存在、不属于所选项目或已删除")
    return row


def _record(row: Any, *, json_fields: set[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif json_fields and column.name in json_fields and isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = []
        result[column.name] = value
    return result


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .")
    return (cleaned[:80] or "NovelAgentStudio").strip()


def _database_path() -> Path | None:
    if engine.url.get_backend_name() != "sqlite" or not engine.url.database:
        return None
    return Path(engine.url.database).expanduser().resolve()


def _database_integrity(db: Session) -> Literal["ok", "failed"]:
    for attempt in range(3):
        result = db.scalar(text("PRAGMA quick_check(1)"))
        if result == "ok":
            return "ok"
        db.rollback()
        if attempt < 2:
            time.sleep(0.025 * (attempt + 1))
    return "failed"
