"""Consistent, recoverable SQLite snapshots for risky local mutations."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine


def _safe_label(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return cleaned[:80] or "snapshot"


def create_sqlite_snapshot(bind: Engine, label: str) -> Path | None:
    """Create a SQLite online-backup snapshot and atomically publish it.

    In-memory and non-SQLite test databases deliberately return ``None``.
    SQLite's backup API copies a transactionally consistent view, including
    committed WAL pages, without ever copying a half-written database file.
    """

    if bind.dialect.name != "sqlite":
        return None
    database = bind.url.database
    if not database or database == ":memory:":
        return None
    source_path = Path(database).expanduser().resolve()
    if not source_path.is_file():
        return None

    backup_dir = source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = backup_dir / f"{stamp}-{_safe_label(label)}.sqlite3"
    temporary = destination.with_suffix(".sqlite3.tmp")

    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(temporary)
    try:
        source.backup(target)
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target.commit()
    finally:
        target.close()
        source.close()
    os.replace(temporary, destination)
    return destination


def create_session_snapshot(session: Any, label: str) -> Path | None:
    return create_sqlite_snapshot(session.get_bind(), label)
