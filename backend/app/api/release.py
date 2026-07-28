from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

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
    StorageCleanupRead,
    StorageReportRead,
)
from app.services.release_backup import (
    ENCRYPTED_BACKUP_OVERHEAD,
    create_backup_archive_file,
    decrypt_backup_archive_file,
    encrypt_backup_archive_file,
    preview_backup_archive,
    restore_backup_archive,
)
from app.services.release_exports import build_export, release_status
from app.services.storage_management import cleanup_storage, storage_report

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
def download_backup(request: Request, db: Session = Depends(get_db)) -> FileResponse:
    password = _backup_password(request)
    archive_path: Path | None = None
    try:
        archive_path = create_backup_archive_file(db)
        if password is not None:
            encrypted_path = encrypt_backup_archive_file(archive_path, password)
            archive_path.unlink(missing_ok=True)
            archive_path = encrypted_path
    except ValueError as exc:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)
        raise
    encrypted = password is not None
    return FileResponse(
        archive_path,
        filename=(
            "NovelAgentStudio-Complete-Backup.nasbackup.enc"
            if encrypted
            else "NovelAgentStudio-Complete-Backup.nasbackup.zip"
        ),
        media_type="application/octet-stream" if encrypted else "application/zip",
        headers={"X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@router.post("/backup/preview", response_model=BackupPreviewRead)
async def preview_backup(
    request: Request, db: Session = Depends(get_db)
) -> BackupPreviewRead:
    archive_path = await _read_backup_upload(request)
    try:
        return await asyncio.to_thread(
            _preview_backup_in_worker,
            cast(Engine, db.get_bind()),
            archive_path,
            _backup_password(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        archive_path.unlink(missing_ok=True)


@router.post("/backup/restore", response_model=BackupRestoreRead)
async def restore_backup(
    request: Request,
    strategy: RestoreStrategy = Query(...),
    expected_sha256: str = Query(..., pattern=r"^[a-f0-9]{64}$"),
    db: Session = Depends(get_db),
) -> BackupRestoreRead:
    archive_path = await _read_backup_upload(request)
    try:
        return await asyncio.to_thread(
            _restore_backup_in_worker,
            cast(Engine, db.get_bind()),
            archive_path,
            _backup_password(request),
            strategy,
            expected_sha256,
        )
    except ValueError as exc:
        detail = str(exc)
        code = 409 if any(
            marker in detail
            for marker in ("SHA-256", "不是空库", "仍在运行", "等待审批")
        ) else 422
        raise HTTPException(status_code=code, detail=detail) from exc
    finally:
        archive_path.unlink(missing_ok=True)


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


@router.get("/storage", response_model=StorageReportRead)
def read_storage_report(
    project_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> StorageReportRead:
    return storage_report(db, project_id=project_id)


@router.post("/storage/cleanup", response_model=StorageCleanupRead)
def run_storage_cleanup(
    dry_run: bool = Query(default=True),
    project_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> StorageCleanupRead:
    try:
        with db.begin():
            return cleanup_storage(db, dry_run=dry_run, project_id=project_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _read_backup_upload(request: Request) -> Path:
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
        if declared > settings.max_backup_bytes + ENCRYPTED_BACKUP_OVERHEAD:
            raise HTTPException(status_code=413, detail="备份文件超过上传大小限制")
    descriptor, raw_path = tempfile.mkstemp(
        prefix="novel-agent-studio-upload-", suffix=".nasbackup.zip"
    )
    os.close(descriptor)
    archive_path = Path(raw_path)
    size = 0
    try:
        with archive_path.open("wb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_backup_bytes + ENCRYPTED_BACKUP_OVERHEAD:
                    raise HTTPException(status_code=413, detail="备份文件超过上传大小限制")
                target.write(chunk)
        if not size:
            raise HTTPException(status_code=422, detail="备份文件为空")
        return archive_path
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise


def _backup_password(request: Request) -> str | None:
    password = request.headers.get("x-nas-backup-password")
    if password is None or password == "":
        return None
    encoded = password.encode("utf-8")
    if len(encoded) < 8:
        raise HTTPException(status_code=422, detail="备份密码至少需要 8 个字节")
    if len(encoded) > 1024:
        raise HTTPException(status_code=422, detail="备份密码过长")
    return password


def _preview_backup_in_worker(
    engine: Engine,
    archive_path: Path,
    password: str | None,
) -> BackupPreviewRead:
    prepared_path, temporary_plaintext = decrypt_backup_archive_file(
        archive_path, password
    )
    try:
        with Session(engine) as db:
            return preview_backup_archive(db, prepared_path)
    finally:
        if temporary_plaintext:
            prepared_path.unlink(missing_ok=True)


def _restore_backup_in_worker(
    engine: Engine,
    archive_path: Path,
    password: str | None,
    strategy: RestoreStrategy,
    expected_sha256: str,
) -> BackupRestoreRead:
    prepared_path, temporary_plaintext = decrypt_backup_archive_file(
        archive_path, password
    )
    try:
        with Session(engine) as db, db.begin():
            return restore_backup_archive(
                db,
                prepared_path,
                strategy=strategy,
                expected_sha256=expected_sha256,
            )
    finally:
        if temporary_plaintext:
            prepared_path.unlink(missing_ok=True)


def _download_response(content: bytes, filename: str, media_type: str) -> Response:
    fallback = "NovelAgentStudio-export"
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename)}'
        ),
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=content, media_type=media_type, headers=headers)
