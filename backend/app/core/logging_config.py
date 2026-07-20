from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.config import get_settings
from app.schemas.release import LogCleanupRead


_REDACTIONS = (
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(
        r"(?i)\b(authorization|cookie|api[-_ ]?key|password|access[-_ ]?token)"
        r"\s*[:=]\s*([^\s,;]+)"
    ),
)


def redact_log_text(value: str) -> str:
    result = value
    for index, pattern in enumerate(_REDACTIONS):
        if index < 3:
            result = pattern.sub("[REDACTED]", result)
        else:
            result = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", result)
    return result


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


def configure_logging() -> None:
    settings = get_settings()
    log_dir = settings.log_path
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    marker = str(log_dir / "studio.log")
    if any(getattr(handler, "baseFilename", None) == marker for handler in root.handlers):
        return

    handler = TimedRotatingFileHandler(
        marker,
        when="midnight",
        backupCount=settings.log_retention_days,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)


def list_log_files() -> list[Path]:
    root = get_settings().log_path
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_file() and (path.name == "studio.log" or path.name.startswith("studio.log."))
    )


def cleanup_log_files(*, delete_all: bool = False) -> LogCleanupRead:
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.log_retention_days)
    deleted = 0
    retained = 0
    for path in list_log_files():
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        should_delete = delete_all or modified < cutoff
        if not should_delete:
            retained += 1
            continue
        try:
            path.unlink()
        except PermissionError:
            # Windows may keep the active log open; truncation still fulfills one-click deletion.
            path.write_text("", encoding="utf-8")
        deleted += 1
    return LogCleanupRead(
        deleted_files=deleted,
        retained_files=retained,
        completed_at=datetime.now(timezone.utc),
    )
