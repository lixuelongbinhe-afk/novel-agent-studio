from collections.abc import Generator
from pathlib import Path
import threading
import time
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
if settings.database_url.startswith("sqlite:///./"):
    Path("data").mkdir(exist_ok=True)

connect_args = (
    {
        "check_same_thread": False,
        "timeout": settings.sqlite_busy_timeout_ms / 1000,
    }
    if settings.database_url.startswith("sqlite")
    else {}
)
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
_checkpoint_lock = threading.Lock()
_checkpoint_count = 0
_checkpoint_duration_seconds = 0.0
_checkpoint_last_result: tuple[int, int, int] | None = None


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection: Any, connection_record: object) -> None:
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(
            f"PRAGMA wal_autocheckpoint={settings.sqlite_wal_autocheckpoint_pages}"
        )
        cursor.close()


def checkpoint_sqlite(*, truncate: bool = False) -> tuple[int, int, int] | None:
    global _checkpoint_count, _checkpoint_duration_seconds, _checkpoint_last_result
    if not settings.database_url.startswith("sqlite"):
        return None
    mode = "TRUNCATE" if truncate else "PASSIVE"
    started = time.perf_counter()
    with engine.connect() as connection:
        row = connection.exec_driver_sql(f"PRAGMA wal_checkpoint({mode})").one()
    result = int(row[0]), int(row[1]), int(row[2])
    with _checkpoint_lock:
        _checkpoint_count += 1
        _checkpoint_duration_seconds += time.perf_counter() - started
        _checkpoint_last_result = result
    return result


def sqlite_runtime_metrics() -> dict[str, Any]:
    database_path: Path | None = None
    if settings.database_url.startswith("sqlite:///"):
        raw_path = settings.database_url.removeprefix("sqlite:///")
        if raw_path and raw_path != ":memory:":
            database_path = Path(raw_path).expanduser().resolve()
    with _checkpoint_lock:
        checkpoint_count = _checkpoint_count
        checkpoint_duration_ms = round(_checkpoint_duration_seconds * 1000, 3)
        checkpoint_last_result = _checkpoint_last_result
    return {
        "database_size": (
            database_path.stat().st_size
            if database_path is not None and database_path.exists()
            else 0
        ),
        "wal_file_size": (
            Path(f"{database_path}-wal").stat().st_size
            if database_path is not None and Path(f"{database_path}-wal").exists()
            else 0
        ),
        "checkpoint_count": checkpoint_count,
        "checkpoint_duration_ms": checkpoint_duration_ms,
        "checkpoint_last_result": checkpoint_last_result,
    }


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
