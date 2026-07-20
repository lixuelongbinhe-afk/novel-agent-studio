import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app import models
from app.core.text import extract_visible_text


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")

def word_count(text: str) -> int:
    visible_text = extract_visible_text(text)
    return len(_CJK_RE.findall(visible_text)) + len(_WORD_RE.findall(visible_text))


def get_or_404(db: Session, model: type[Any], item_id: int) -> Any:
    item = db.get(model, item_id)
    if item is None or getattr(item, "deleted_at", None) is not None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return item


def get_including_deleted_or_404(db: Session, model: type[Any], item_id: int) -> Any:
    item = db.get(model, item_id)
    if item is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return item


def list_active(db: Session, model: type[Any], *filters: ColumnElement[bool]) -> list[Any]:
    order = [model.position, model.id] if hasattr(model, "position") else [model.id]
    stmt = select(model).where(model.deleted_at.is_(None), *filters).order_by(*order)
    return list(db.scalars(stmt).all())


def list_deleted(db: Session, model: type[Any], *filters: ColumnElement[bool]) -> list[Any]:
    stmt = select(model).where(model.deleted_at.is_not(None), *filters).order_by(model.id)
    return list(db.scalars(stmt).all())


def require_revision(item: Any, expected_revision: int) -> None:
    if item.revision != expected_revision:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail={
                "message": "Record revision conflict",
                "current_revision": item.revision,
                "record_id": item.id,
            },
        )


def soft_delete(item: Any) -> None:
    item.deleted_at = datetime.now(timezone.utc)
    item.revision += 1


def restore(item: Any) -> None:
    item.deleted_at = None
    item.revision += 1


def create_seed_data(db: Session) -> models.Project:
    from app.services.models import ensure_provider_presets

    ensure_provider_presets(db)
    existing = db.scalar(
        select(models.Project).where(
            models.Project.title == "雾港回声",
            models.Project.deleted_at.is_(None),
        )
    )
    if existing is not None:
        provider = _ensure_mock_provider(db)
        from app.services.context_memory import (
            ensure_default_context_policy,
            ensure_provider_data_policy,
        )

        ensure_default_context_policy(db, existing.id)
        ensure_provider_data_policy(db, provider.id)
        from app.services.workflows import ensure_default_mock_workflow

        ensure_default_mock_workflow(db, existing.id)
        return existing

    project = models.Project(
        title="雾港回声",
        summary="一座沿海旧城在持续雾季中醒来，年轻档案员追查失踪航线与家族秘密。",
        target_words=120000,
    )
    db.add(project)
    db.flush()
    volume = models.Volume(project_id=project.id, title="第一卷：雾灯", position=1)
    db.add(volume)
    db.flush()
    chapter = models.Chapter(
        volume_id=volume.id,
        title="第一章 返航前夜",
        content="雾从码头爬上钟楼时，林栀第一次听见旧无线电里传来自己的名字。",
        position=1,
    )
    chapter.word_count = word_count(chapter.content)
    db.add(chapter)
    db.add(models.StoryEntity(project_id=project.id, name="林栀", kind="character", description="年轻档案员，擅长复原旧航海日志。", tags=json.dumps(["主角", "档案馆"])))
    db.add(models.StyleGuide(project_id=project.id, name="叙述口吻", rule_text="克制、冷峻，保留海雾与机械噪声的感官细节。"))
    provider = _ensure_mock_provider(db)
    from app.services.context_memory import (
        ensure_default_context_policy,
        ensure_provider_data_policy,
    )

    ensure_default_context_policy(db, project.id)
    ensure_provider_data_policy(db, provider.id)
    from app.services.workflows import ensure_default_mock_workflow

    ensure_default_mock_workflow(db, project.id)
    return project


def _ensure_mock_provider(db: Session) -> models.ProviderAccount:
    provider = db.scalar(
        select(models.ProviderAccount).where(models.ProviderAccount.name == "Mock Provider")
    )
    if provider is None:
        provider = models.ProviderAccount(
            name="Mock Provider", provider_type="mock", credential_env_var=None
        )
        db.add(provider)
        db.flush()
    protocol = db.scalar(
        select(models.ProtocolConfiguration).where(
            models.ProtocolConfiguration.provider_account_id == provider.id
        )
    )
    if protocol is None:
        db.add(
            models.ProtocolConfiguration(
                provider_account_id=provider.id, protocol="mock", options_json="{}"
            )
        )
    profile = db.scalar(
        select(models.ModelProfile).where(
            models.ModelProfile.provider_account_id == provider.id,
            models.ModelProfile.name == "mock-novel-v1",
        )
    )
    if profile is None:
        profile = models.ModelProfile(
            provider_account_id=provider.id,
            name="mock-novel-v1",
            display_name="Mock Novel v1",
            context_window=8192,
        )
        db.add(profile)
        db.flush()
        db.add_all(
            [
                models.ModelCapability(model_profile_id=profile.id, capability="streaming"),
                models.ModelCapability(model_profile_id=profile.id, capability="structured_json"),
                models.ModelPricing(
                    model_profile_id=profile.id,
                    input_per_million=None,
                    output_per_million=None,
                ),
            ]
        )
    return provider
