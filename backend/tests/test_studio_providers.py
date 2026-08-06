from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.schemas.studio import ProviderSetup
from app.services import studio_providers


@pytest.fixture
def db(tmp_path: Path) -> Generator[Session, None, None]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'providers.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        yield session
    engine.dispose()


def test_provider_lifecycle_stays_inside_provider_service(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: list[tuple[int, str]] = []
    deleted: list[int] = []
    monkeypatch.setattr(
        studio_providers,
        "set_provider_secret",
        lambda provider_id, secret: stored.append((provider_id, secret)),
    )
    monkeypatch.setattr(
        studio_providers,
        "delete_provider_secret",
        lambda provider_id: deleted.append(provider_id),
    )
    monkeypatch.setattr(
        studio_providers,
        "has_provider_secret",
        lambda provider_id: any(item[0] == provider_id for item in stored),
    )

    provider = studio_providers.setup_provider(
        db,
        ProviderSetup(
            preset="openai_compatible",
            name="Local gateway",
            base_url="http://127.0.0.1:8000/v1",
            model="local-model",
            api_key="initial-secret",
        ),
    )
    provider_id = int(provider["id"])

    assert stored == [(provider_id, "initial-secret")]
    assert studio_providers.list_studio_providers(db)[0]["models"][0]["name"] == "local-model"

    updated = studio_providers.update_provider_secret(db, provider_id, "replacement")
    assert updated["secret_stored"] is True
    assert stored[-1] == (provider_id, "replacement")

    studio_providers.delete_studio_provider(db, provider_id)
    assert deleted == [provider_id]
    assert studio_providers.list_studio_providers(db) == []
