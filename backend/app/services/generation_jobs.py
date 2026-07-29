from __future__ import annotations

from dataclasses import dataclass
from time import sleep

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app import models


ACTIVE_GENERATION_STATUSES = ("queued", "running")


@dataclass(frozen=True)
class GenerationLease:
    job: models.GenerationJob
    replayed: bool


class GenerationLeaseConflict(RuntimeError):
    pass


def scope_key(project_id: int, phase: str, chapter_id: int | None, mode: str) -> str:
    return f"project:{project_id}:phase:{phase}:chapter:{chapter_id or 0}:mode:{mode}"


def acquire(
    db: Session,
    *,
    project_id: int,
    phase: str,
    chapter_id: int | None,
    mode: str,
    idempotency_key: str,
    label: str,
    model_name: str,
    model_reason: str,
) -> GenerationLease:
    existing = _by_idempotency_key(db, project_id, idempotency_key)
    if existing is not None:
        return GenerationLease(existing, True)

    scope = scope_key(project_id, phase, chapter_id, mode)
    active = _active_in_scope(db, scope)
    if active is not None:
        return GenerationLease(active, True)

    job = models.GenerationJob(
        project_id=project_id,
        kind=phase,
        label=label,
        status="running",
        progress=5,
        model_name=model_name,
        model_reason=model_reason,
        idempotency_key=idempotency_key,
        active_scope_key=scope,
    )
    db.add(job)
    try:
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        for _ in range(20):
            db.rollback()
            winner = _by_idempotency_key(
                db, project_id, idempotency_key
            ) or _active_in_scope(db, scope)
            if winner is not None:
                return GenerationLease(winner, True)
            sleep(0.05)
        raise GenerationLeaseConflict(
            "Another generation request holds this scope but its lease is not visible"
        ) from exc
    db.refresh(job)
    return GenerationLease(job, False)


def complete(
    db: Session, job: models.GenerationJob, *, result_artifact_id: int
) -> None:
    job.result_artifact_id = result_artifact_id
    job.status = "completed"
    job.progress = 100
    job.error_message = ""
    job.active_scope_key = None
    job.revision += 1


def fail(db: Session, job_id: int, message: str, *, cancelled: bool = False) -> None:
    db.rollback()
    job = db.get(models.GenerationJob, job_id)
    if job is None:
        return
    job.status = "cancelled" if cancelled else "failed"
    job.error_message = message[:2000]
    job.progress = 100
    job.active_scope_key = None
    job.revision += 1
    db.commit()


def failure_message(reason: str, completed: int, total: int) -> str:
    progress = f"{completed}/{total}" if total > 0 else "准备阶段"
    return (
        f"生成失败（已完成模型调用 {progress}）。模型调用与已产生的费用记录已保留；"
        f"业务产出和阶段推进未提交，可以安全重试。原因：{reason}"
    )[:2000]


def _by_idempotency_key(
    db: Session, project_id: int, idempotency_key: str
) -> models.GenerationJob | None:
    return db.scalar(
        select(models.GenerationJob).where(
            models.GenerationJob.project_id == project_id,
            models.GenerationJob.idempotency_key == idempotency_key,
            models.GenerationJob.deleted_at.is_(None),
        )
    )


def _active_in_scope(db: Session, scope: str) -> models.GenerationJob | None:
    return db.scalar(
        select(models.GenerationJob)
        .where(
            models.GenerationJob.active_scope_key == scope,
            models.GenerationJob.status.in_(ACTIVE_GENERATION_STATUSES),
            models.GenerationJob.deleted_at.is_(None),
        )
        .order_by(models.GenerationJob.id)
    )
