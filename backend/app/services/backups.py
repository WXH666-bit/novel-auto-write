"""Consistent, recoverable SQLite snapshots for risky local mutations."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from ..config import DATA_DIR


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


def create_session_snapshot(
    session: Any,
    label: str,
    *,
    project_id: str | None = None,
    owner_id: str | None = None,
) -> Path | None:
    """Snapshot a risky mutation for either supported database backend.

    SQLite can copy the whole local database transactionally.  On MySQL the
    application instead writes a tenant/project ZIP immediately before review
    acceptance; infrastructure migrations use the separate mysqldump helper.
    """

    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        return create_sqlite_snapshot(bind, label)
    if bind.dialect.name != "mysql" or not project_id or not owner_id:
        return None

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    from .exports import export_project_zip

    backup_dir = (DATA_DIR / "backups" / owner_id / project_id).resolve()
    data_root = DATA_DIR.resolve()
    if data_root not in backup_dir.parents:
        raise ValueError("备份路径越过数据目录")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = backup_dir / f"{stamp}-{_safe_label(label)}.zip"
    temporary = destination.with_suffix(".zip.tmp")
    # The caller may already have claimed a review row inside an uncommitted
    # transaction.  Export through an independent read session so the archive
    # is a true pre-commit view and never contains transient "committing"
    # state.
    snapshot_engine = bind if isinstance(bind, Engine) else bind.engine
    with Session(bind=snapshot_engine, autoflush=False, expire_on_commit=False) as snapshot_session:
        payload = export_project_zip(snapshot_session, project_id, owner_id=owner_id)
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    return destination
