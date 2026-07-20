from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging_config import cleanup_log_files
from app.database import get_db
from app.schemas.release import (
    BackupPreviewRead,
    BackupRestoreRead,
    ExportKind,
    LogCleanupRead,
    ReleaseStatusRead,
    RestoreStrategy,
)
from app.services.release_backup import (
    create_backup_archive,
    preview_backup_archive,
    restore_backup_archive,
)
from app.services.release_exports import build_export, release_status


router = APIRouter(prefix="/release", tags=["release"])
_ZIP_MEDIA_TYPES = frozenset(
    {"application/zip", "application/x-zip-compressed", "application/octet-stream"}
)


@router.get("/status", response_model=ReleaseStatusRead)
def read_release_status(
    request: Request, db: Session = Depends(get_db)
) -> ReleaseStatusRead:
    return release_status(
        db, frontend_bundled=bool(getattr(request.app.state, "frontend_bundled", False))
    )


@router.get("/backup")
def download_backup(db: Session = Depends(get_db)) -> Response:
    try:
        content = create_backup_archive(db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _download_response(
        content, "NovelAgentStudio-Complete-Backup.nasbackup.zip", "application/zip"
    )


@router.post("/backup/preview", response_model=BackupPreviewRead)
async def preview_backup(
    request: Request, db: Session = Depends(get_db)
) -> BackupPreviewRead:
    content = await _read_backup_upload(request)
    try:
        return preview_backup_archive(db, content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/backup/restore", response_model=BackupRestoreRead)
async def restore_backup(
    request: Request,
    strategy: RestoreStrategy = Query(...),
    expected_sha256: str = Query(..., pattern=r"^[a-f0-9]{64}$"),
    db: Session = Depends(get_db),
) -> BackupRestoreRead:
    content = await _read_backup_upload(request)
    try:
        with db.begin():
            return restore_backup_archive(
                db,
                content,
                strategy=strategy,
                expected_sha256=expected_sha256,
            )
    except ValueError as exc:
        detail = str(exc)
        code = 409 if any(
            marker in detail
            for marker in ("SHA-256", "不是空库", "仍在运行", "等待审批")
        ) else 422
        raise HTTPException(status_code=code, detail=detail) from exc


@router.get("/exports/{kind}")
def download_export(
    kind: ExportKind,
    request: Request,
    project_id: int | None = Query(default=None, ge=1),
    chapter_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> Response:
    try:
        artifact = build_export(
            db,
            kind,
            project_id=project_id,
            chapter_id=chapter_id,
            frontend_bundled=bool(getattr(request.app.state, "frontend_bundled", False)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _download_response(artifact.content, artifact.filename, artifact.media_type)


@router.post("/logs/cleanup", response_model=LogCleanupRead)
def cleanup_expired_logs() -> LogCleanupRead:
    return cleanup_log_files(delete_all=False)


@router.delete("/logs", response_model=LogCleanupRead)
def delete_all_logs() -> LogCleanupRead:
    return cleanup_log_files(delete_all=True)


async def _read_backup_upload(request: Request) -> bytes:
    settings = get_settings()
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in _ZIP_MEDIA_TYPES:
        raise HTTPException(status_code=415, detail="备份上传必须是 ZIP 文件")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length 无效") from exc
        if declared > settings.max_backup_bytes:
            raise HTTPException(status_code=413, detail="备份文件超过上传大小限制")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > settings.max_backup_bytes:
            raise HTTPException(status_code=413, detail="备份文件超过上传大小限制")
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(status_code=422, detail="备份文件为空")
    return b"".join(chunks)


def _download_response(content: bytes, filename: str, media_type: str) -> Response:
    fallback = "NovelAgentStudio-export"
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename)}'
        ),
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=content, media_type=media_type, headers=headers)
