"""SQLite engine, sessions, and the explainable full-text indexes."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import DATABASE_URL


class Base(DeclarativeBase):
    """Base for all persisted application models."""


def create_engine_for_url(url: str | None = None) -> Engine:
    url = url or DATABASE_URL
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url.endswith(":memory:"):
            kwargs["poolclass"] = StaticPool
    db_engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(db_engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            # Foreign keys must be enabled for every SQLite connection.
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL is safe for the single-user desktop app and makes a reader
            # usable while a generation job persists an artifact.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return db_engine


engine = create_engine_for_url()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def _create_fts_tables(connection: Any) -> None:
    """Create standalone FTS5 tables.

    Standalone tables (rather than external-content tables) let us rebuild
    safely after an import or migration and keep source IDs available for UI
    citations.  All table names and SQL are constants; user text is always
    supplied as bound parameters during indexing.
    """

    chapter_fts_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(chapter_fts)").fetchall()
    }
    if chapter_fts_columns and "revision_id" not in chapter_fts_columns:
        # FTS is derived state, so replacing an older virtual-table shape is
        # safer and simpler than attempting an in-place virtual-table change.
        connection.exec_driver_sql("DROP TABLE chapter_fts")
    connection.exec_driver_sql(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chapter_fts USING fts5(
            chapter_id UNINDEXED,
            revision_id UNINDEXED,
            project_id UNINDEXED,
            title,
            content,
            summary,
            tokenize='unicode61'
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS canon_fts USING fts5(
            canon_item_id UNINDEXED,
            project_id UNINDEXED,
            item_key,
            value,
            source_excerpt,
            tokenize='unicode61'
        )
        """
    )


def _ensure_sqlite_columns(connection: Any) -> None:
    """Apply tiny additive migrations for databases created by an older build.

    The desktop app has no concurrent migration runner.  Additive columns are
    therefore checked and added during startup; all statements below are
    constants and never contain user input.  A consistent backup is the
    caller's responsibility before a production migration.
    """

    additions = {
        "projects": {
            "story_bible": "TEXT",
            "source_hash": "VARCHAR(64)",
            "source_filename": "VARCHAR(255)",
            "source_encoding": "VARCHAR(40)",
            "memory_epoch": "INTEGER NOT NULL DEFAULT 0",
        },
        "chapters": {
            "source_type": "VARCHAR(40)",
            "accepted_revision_id": "VARCHAR(36)",
        },
        "canon_items": {"aliases": "JSON NOT NULL DEFAULT '[]'"},
        "generation_runs": {
            "context_snapshot": "JSON NOT NULL DEFAULT '{}'",
            "review_bundle_id": "VARCHAR(36)",
        },
        "review_bundles": {"base_memory_epoch": "INTEGER NOT NULL DEFAULT 0"},
    }
    for table, columns in additions.items():
        existing = {
            row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()
        }
        for column, definition in columns.items():
            if column not in existing:
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'
                )


def rebuild_search_index(session: Session | None = None, db_engine: Engine | None = None) -> None:
    """Rebuild both FTS indexes from committed relational data.

    The operation is intentionally idempotent.  It can be run after a crash,
    import, or schema migration without changing the story canon itself.
    """

    own_session = session is None
    active_engine = db_engine or (session.get_bind() if session is not None else engine)
    if own_session:
        session = SessionLocal(bind=active_engine)
    assert session is not None
    try:
        # Import here to avoid a Base/model import cycle during module import.
        from .models import CanonItem, Chapter, ChapterRevision

        with active_engine.begin() as connection:
            _create_fts_tables(connection)
            connection.exec_driver_sql("DELETE FROM chapter_fts")
            connection.exec_driver_sql("DELETE FROM canon_fts")

            # A small Python map is safer than a correlated query across
            # SQLite versions, and the index is local.
            revisions_by_id: dict[str, str] = {}
            for revision in session.query(ChapterRevision).order_by(
                ChapterRevision.revision_number.asc()
            ):
                revisions_by_id[revision.id] = revision.content
            chapters = session.query(Chapter).all()
            for chapter in chapters:
                revision_id = chapter.accepted_revision_id
                if not revision_id or revision_id not in revisions_by_id:
                    continue
                connection.exec_driver_sql(
                    "INSERT INTO chapter_fts(chapter_id, revision_id, project_id, title, content, summary) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        chapter.id,
                        revision_id,
                        chapter.project_id,
                        chapter.title or "",
                        revisions_by_id[revision_id],
                        chapter.summary or "",
                    ),
                )
            for item in session.query(CanonItem).filter(
                CanonItem.status.in_(("confirmed", "active", "已确认"))
            ):
                connection.exec_driver_sql(
                    "INSERT INTO canon_fts(canon_item_id, project_id, item_key, value, source_excerpt) VALUES (?, ?, ?, ?, ?)",
                    (
                        item.id,
                        item.project_id,
                        item.key or "",
                        item.value_text,
                        item.source_excerpt or "",
                    ),
                )
    finally:
        if own_session:
            session.close()


def init_db(db_engine: Engine | None = None) -> Engine:
    """Create the schema and safely initialise FTS5 indexes.

    Calling this at every application start is safe.  ``create_all`` never
    mutates existing rows, and the index rebuild only recreates derived data.
    """

    active_engine = db_engine or engine
    from . import models  # noqa: F401  # register all mapped classes

    Base.metadata.create_all(active_engine)
    if active_engine.dialect.name == "sqlite":
        with active_engine.connect() as connection:
            migration_needed = any(
                column
                not in {
                    row[1]
                    for row in connection.exec_driver_sql(
                        f'PRAGMA table_info("{table}")'
                    ).fetchall()
                }
                for table, columns in {
                    "projects": {
                        "story_bible",
                        "source_hash",
                        "source_filename",
                        "source_encoding",
                        "memory_epoch",
                    },
                    "chapters": {"source_type", "accepted_revision_id"},
                    "canon_items": {"aliases"},
                    "generation_runs": {"context_snapshot", "review_bundle_id"},
                    "review_bundles": {"base_memory_epoch"},
                }.items()
                for column in columns
            )
        if migration_needed:
            from .services.backups import create_sqlite_snapshot

            create_sqlite_snapshot(active_engine, "before-schema-migration")
    with active_engine.begin() as connection:
        _ensure_sqlite_columns(connection)
        connection.exec_driver_sql(
            "UPDATE chapters SET accepted_revision_id = current_revision_id "
            "WHERE accepted_revision_id IS NULL AND current_revision_id IS NOT NULL "
            "AND status IN ('confirmed', 'accepted', 'published', 'committed')"
        )
        _create_fts_tables(connection)
    rebuild_search_index(db_engine=active_engine)
    return active_engine


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding one short-lived synchronous Session."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
