from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models
from app.database import Base
from app.schemas.studio import ProviderSetup
from app.services import studio_providers
from app.services.errors import ConflictError


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


def test_setup_provider_restores_a_soft_deleted_provider(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: list[tuple[int, str]] = []
    monkeypatch.setattr(
        studio_providers,
        "set_provider_secret",
        lambda provider_id, secret: stored.append((provider_id, secret)),
    )
    monkeypatch.setattr(studio_providers, "delete_provider_secret", lambda _provider_id: None)
    monkeypatch.setattr(studio_providers, "has_provider_secret", lambda _provider_id: True)
    payload = ProviderSetup(
        preset="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-v4-flash",
        api_key="first-secret",
    )

    original = studio_providers.setup_provider(db, payload)
    provider_id = int(original["id"])
    studio_providers.delete_studio_provider(db, provider_id)

    restored = studio_providers.setup_provider(
        db,
        payload.model_copy(update={"api_key": "replacement-secret"}),
    )

    assert restored["id"] == provider_id
    assert stored[-1] == (provider_id, "replacement-secret")
    provider = db.get(models.ProviderAccount, provider_id)
    assert provider is not None
    assert provider.deleted_at is None
    assert provider.enabled is True
    assert provider.revision == 3
    assert [item["id"] for item in studio_providers.list_studio_providers(db)] == [
        provider_id
    ]
    protocol = db.scalar(
        select(models.ProtocolConfiguration).where(
            models.ProtocolConfiguration.provider_account_id == provider_id
        )
    )
    assert protocol is not None and protocol.protocol == "openai_chat"


def test_setup_provider_still_rejects_an_active_duplicate(db: Session) -> None:
    payload = ProviderSetup(
        preset="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-v4-flash",
        env_var_name="DEEPSEEK_API_KEY",
    )
    studio_providers.setup_provider(db, payload)

    with pytest.raises(ConflictError, match="Provider"):
        studio_providers.setup_provider(db, payload)
