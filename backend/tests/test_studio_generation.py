from collections.abc import Generator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.schemas.studio import GenerateRequest, StudioProjectCreate
from app.services import studio, studio_generation


@pytest.fixture
def db(tmp_path: Path) -> Generator[Session, None, None]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'generation.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        yield session
    engine.dispose()


def test_validation_returns_a_frozen_assertable_generation_request(
    db: Session,
) -> None:
    overview = studio.create_project(
        db,
        StudioProjectCreate(
            title="雾港回声",
            idea="档案员调查一艘失踪渡轮。",
            entry_mode="creative",
            chapter_count=4,
        ),
    )
    payload = GenerateRequest(
        idempotency_key="pipeline-validation",
        use_demo_model=True,
        agent_name="定位与主题策划",
    )

    request = studio_generation._validate_generation_request(
        db, overview.project.id, "world", payload
    )

    assert request.phase_agents == (
        ("定位与主题策划", "明确题材定位、目标读者、核心主题、叙事基调与篇幅策略。"),
    )
    assert request.profile is None
    assert "演示" in request.reason
    assert request.chapter_ranges == ((1, 1),)
    assert request.total_calls == 1
    with pytest.raises(FrozenInstanceError):
        request.__setattr__("reason", "changed")


def test_failure_message_reports_pipeline_progress() -> None:
    message = studio_generation._generation_failure_message("上游超时", 2, 6)

    assert "2/6" in message
    assert "上游超时" in message
