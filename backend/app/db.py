"""Database engines, Alembic bootstrap, sessions, and search projections."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
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
    elif url.startswith("mysql"):
        kwargs["pool_recycle"] = 1800

    db_engine = create_engine(url, **kwargs)
    if db_engine.dialect.name == "sqlite":

        @event.listens_for(db_engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    if db_engine.dialect.name == "mysql":

        @event.listens_for(db_engine, "connect")
        def _mysql_session(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("SET time_zone = '+00:00'")
            cursor.execute("SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            cursor.close()

    return db_engine


engine = create_engine_for_url()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def _alembic_config() -> Config:
    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "backend" / "alembic"))
    return config


def _current_revision(db_engine: Engine) -> str | None:
    if "alembic_version" not in inspect(db_engine).get_table_names():
        return None
    with db_engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


def run_migrations(db_engine: Engine | None = None) -> Engine:
    """Upgrade a new or legacy database to Alembic head.

    An original SQLite file is snapshotted before the first schema mutation.
    The migration itself detects the legacy table shape, creates a disabled
    legacy owner, and never rewrites story content.
    """

    active_engine = db_engine or engine
    config = _alembic_config()
    head = ScriptDirectory.from_config(config).get_current_head()
    current = _current_revision(active_engine)
    table_names = set(inspect(active_engine).get_table_names())
    if (
        active_engine.dialect.name == "sqlite"
        and "projects" in table_names
        and current != head
    ):
        from .services.backups import create_sqlite_snapshot

        create_sqlite_snapshot(active_engine, "before-alembic-migration")

    if active_engine.dialect.name == "sqlite":
        # Batch-rebuilding the parent ``projects`` table while foreign keys are
        # active would make SQLite cascade-delete child story rows when the old
        # table is dropped.  Disable enforcement only on this migration
        # connection, then verify every FK before returning it to the pool.
        with active_engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            try:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                connection.commit()
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                connection.commit()
                if violations:
                    raise RuntimeError("数据库迁移后外键校验失败")
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    else:
        with active_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    return active_engine


def rebuild_search_index(
    session: Session | None = None,
    db_engine: Engine | None = None,
    *,
    owner_id: str | None = None,
    project_id: str | None = None,
) -> None:
    """Compatibility wrapper for the dialect-specific search service."""

    from .services.search import rebuild_search_index as rebuild

    rebuild(
        session,
        engine=db_engine,
        owner_id=owner_id,
        project_id=project_id,
    )


def init_db(db_engine: Engine | None = None) -> Engine:
    """Run formal migrations, repair accepted pointers, and rebuild search."""

    active_engine = run_migrations(db_engine or engine)
    with active_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE chapters SET accepted_revision_id = current_revision_id "
                "WHERE accepted_revision_id IS NULL AND current_revision_id IS NOT NULL "
                "AND status IN ('confirmed', 'accepted', 'published', 'committed')"
            )
        )
    from .services.storage import migrate_import_storage

    with Session(bind=active_engine, autoflush=False, expire_on_commit=False) as session:
        storage_migration = migrate_import_storage(session)
        try:
            session.commit()
        except Exception:
            session.rollback()
            storage_migration.restore()
            raise
        storage_migration.finalize()
    rebuild_search_index(db_engine=active_engine)
    return active_engine


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding one short-lived synchronous Session."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = [
    "Base",
    "SessionLocal",
    "create_engine_for_url",
    "engine",
    "get_db",
    "init_db",
    "rebuild_search_index",
    "run_migrations",
]
