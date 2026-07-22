from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import get_db
from app.schemas.studio import (
    ArtifactDecision,
    ArtifactUpdate,
    ChapterTreeRepairRequest,
    ChatRequest,
    ContinuationImportRequest,
    ContinuationSettingsUpdate,
    GenerateRequest,
    MessageProposalDecision,
    OutlineImportRequest,
    ProviderSecretUpdate,
    ProviderSetup,
    SnapshotCreate,
    StudioProjectCreate,
    StudioStateUpdate,
)
from app.services import document_import, studio


router = APIRouter(prefix="/studio", tags=["studio-v2"])


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return studio.dashboard(db)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: StudioProjectCreate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    with db.begin():
        return studio.create_project(db, payload)


@router.post("/continuations", status_code=status.HTTP_201_CREATED)
def create_continuation(
    payload: ContinuationImportRequest, db: Session = Depends(get_db)
) -> dict[str, Any]:
    with db.begin():
        return studio.create_continuation_project(db, payload)


@router.post("/continuations/file", status_code=status.HTTP_201_CREATED)
async def create_continuation_from_file(
    title: str = Form(...),
    file: UploadFile = File(...),
    target_words: int | None = Form(None),
    target_chapters: int | None = Form(None),
    target_volumes: int | None = Form(None),
    continuation_start: Literal["choose", "current", "next"] = Form("choose"),
    direction_mode: Literal["user", "ai", "switchable"] = Form("switchable"),
    user_outline: str = Form(""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    text = await _read_document(file)
    payload = ContinuationImportRequest(
        title=title,
        text=text,
        source_name=file.filename or "导入正文",
        target_words=target_words,
        target_chapters=target_chapters,
        target_volumes=target_volumes,
        continuation_start=continuation_start,
        direction_mode=direction_mode,
        user_outline=user_outline,
    )
    with db.begin():
        return studio.create_continuation_project(db, payload)


@router.get("/projects/{project_id}")
def read_project(project_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return studio.project_overview(db, project_id)


@router.patch("/projects/{project_id}/state")
def update_project_state(
    project_id: int,
    payload: StudioStateUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    with db.begin():
        return studio.update_state(db, project_id, payload)


@router.patch("/projects/{project_id}/continuation/settings")
def update_continuation_settings(
    project_id: int,
    payload: ContinuationSettingsUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    with db.begin():
        return studio.update_continuation_settings(db, project_id, payload)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: Session = Depends(get_db)) -> Response:
    with db.begin():
        from datetime import datetime, timezone

        project = studio._project(db, project_id)
        project.deleted_at = datetime.now(timezone.utc)
        project.revision += 1
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/artifacts/{artifact_id}")
def update_artifact(
    artifact_id: int,
    payload: ArtifactUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    with db.begin():
        return studio.update_artifact(db, artifact_id, payload)


@router.get("/artifacts/{artifact_id}/versions")
def artifact_versions(artifact_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return studio.artifact_versions(db, artifact_id)


@router.post("/artifacts/{artifact_id}/decision")
def decide_artifact(
    artifact_id: int,
    payload: ArtifactDecision,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    with db.begin():
        return studio.decide_artifact(db, artifact_id, payload)


@router.post("/projects/{project_id}/generate/{phase}")
async def generate_phase(
    project_id: int,
    phase: str,
    payload: GenerateRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await studio.generate(db, project_id, phase, payload)


@router.post("/projects/{project_id}/chat")
async def chat(
    project_id: int,
    payload: ChatRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await studio.chat(db, project_id, payload)


@router.post("/projects/{project_id}/messages/{message_id}/proposal")
async def decide_message_proposal(
    project_id: int,
    message_id: int,
    payload: MessageProposalDecision,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await studio.decide_message_proposal(db, project_id, message_id, payload.action)


@router.post("/projects/{project_id}/outline/preview")
def preview_outline(
    project_id: int,
    payload: OutlineImportRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = studio._project(db, project_id)
    return studio.parse_outline(payload.text, project.title)


@router.post("/projects/{project_id}/outline/preview-file")
async def preview_outline_file(
    project_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = studio._project(db, project_id)
    text = await _read_document(file, allowed={".txt", ".md", ".markdown", ".docx"})
    return {**studio.parse_outline(text, project.title), "source_text": text}


@router.post("/projects/{project_id}/style-reference")
async def upload_style_reference(
    project_id: int,
    file: UploadFile = File(...),
    use_demo_model: bool = Form(False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    studio._project(db, project_id)
    text = await _read_document(file, allowed={".txt", ".md", ".markdown", ".docx"})
    return await studio.extract_style_reference(
        db, project_id, text[:200_000], file.filename or "参考文本", use_demo_model
    )


@router.post("/projects/{project_id}/outline/import")
def import_outline(
    project_id: int,
    payload: OutlineImportRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    with db.begin():
        return studio.import_outline(db, project_id, payload)


@router.post("/projects/{project_id}/snapshots", status_code=status.HTTP_201_CREATED)
def create_snapshot(
    project_id: int,
    payload: SnapshotCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    with db.begin():
        return studio.create_snapshot(db, project_id, payload)


@router.post("/projects/{project_id}/snapshots/{snapshot_id}/restore")
def restore_snapshot(
    project_id: int,
    snapshot_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    with db.begin():
        return studio.restore_snapshot(db, project_id, snapshot_id)


@router.get("/projects/{project_id}/chapter-tree/repair-preview")
def chapter_tree_repair_preview(
    project_id: int, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return studio.chapter_tree_repair_preview(db, project_id)


@router.post("/projects/{project_id}/chapter-tree/repair")
def repair_chapter_tree(
    project_id: int,
    payload: ChapterTreeRepairRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    with db.begin():
        return studio.repair_chapter_tree(db, project_id, payload)


@router.get("/providers")
def list_providers(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return studio.list_studio_providers(db)


@router.post("/providers", status_code=status.HTTP_201_CREATED)
def setup_provider(
    payload: ProviderSetup, db: Session = Depends(get_db)
) -> dict[str, Any]:
    with db.begin():
        return studio.setup_provider(db, payload)


@router.put("/providers/{provider_id}/secret")
def update_provider_secret(
    provider_id: int,
    payload: ProviderSecretUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return studio.update_provider_secret(db, provider_id, payload.api_key)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(provider_id: int, db: Session = Depends(get_db)) -> Response:
    with db.begin():
        studio.delete_studio_provider(db, provider_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _extract_document_text(content: bytes, filename: str) -> str:
    """Backward-compatible synchronous entry point used by tests and tools."""
    try:
        return document_import.extract_document_text(
            content, filename, limits=_import_limits()
        )
    except document_import.DocumentImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _read_document(
    file: UploadFile,
    *,
    allowed: set[str] | None = None,
) -> str:
    limits = _import_limits()
    content = await file.read(limits.max_upload_bytes + 1)
    try:
        return await document_import.extract_document_text_async(
            content,
            file.filename or "导入文件",
            allowed=allowed,
            limits=limits,
        )
    except document_import.DocumentImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _import_limits() -> document_import.ImportLimits:
    settings = get_settings()
    return document_import.ImportLimits(
        max_upload_bytes=settings.max_import_bytes,
        max_text_chars=settings.max_import_text_chars,
        parse_timeout_seconds=settings.import_parse_timeout_seconds,
        docx_max_entries=settings.docx_max_entries,
        docx_max_expanded_bytes=settings.docx_max_expanded_bytes,
        docx_max_member_bytes=settings.docx_max_member_bytes,
        docx_max_compression_ratio=settings.docx_max_compression_ratio,
        pdf_max_pages=settings.pdf_max_pages,
    )
