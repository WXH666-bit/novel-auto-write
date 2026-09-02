"""Database engines, Alembic bootstrap, sessions, and search projections."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from .config import DATABASE_URL

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base for all persisted application models."""


def _env_int(*names: str, default: int, minimum: int = 1) -> int:
    """Read a positive pool setting while keeping legacy env names working.

    Deployments historically used both ``NOVEL_DB_*`` and
    ``NOVEL_MYSQL_*`` prefixes in local manifests.  Supporting both here keeps
    the engine configuration explicit without making a database URL carry
    operational pool policy.  Invalid values fail at startup instead of being
    silently coerced into an unsafe pool.
    """

    raw = next((os.getenv(name) for name in names if os.getenv(name) is not None), None)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{names[0]} 必须是整数") from exc
    if value < minimum:
        raise ValueError(f"{names[0]} 必须大于等于 {minimum}")
    return value


def _sqlite_memory_url(url: str) -> bool:
    """Return whether a SQLite URL points at process-local in-memory storage."""

    # ``sqlite://`` and ``sqlite:///:memory:`` are both accepted by SQLAlchemy
    # as memory databases.  Parse the URL rather than checking a suffix so URI
    # forms such as ``file::memory:?mode=memory`` are classified correctly too.
    try:
        parsed = make_url(url)
    except (TypeError, ValueError):
        return False
    if parsed.drivername.split("+", 1)[0].lower() != "sqlite":
        return False
    database = str(parsed.database or "").strip().lower()
    query = {str(key).lower(): str(value).lower() for key, value in parsed.query.items()}
    return (
        not database
        or database == ":memory:"
        or database.startswith("file::memory:")
        or query.get("mode") == "memory"
    )


def create_engine_for_url(url: str | None = None) -> Engine:
    url = url or DATABASE_URL
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if _sqlite_memory_url(url):
            kwargs["poolclass"] = StaticPool
        else:
            # A file-backed SQLite connection must not be held by a long-lived
            # QueuePool checkout.  SSE polls, background jobs, and ordinary
            # requests all get short-lived independent connections, while WAL
            # and busy_timeout below provide the intended write coordination.
            kwargs["poolclass"] = NullPool
    elif url.startswith("mysql"):
        # Keep MySQL operational settings explicit and deployment-controlled.
        # The defaults are conservative for a single app host, but every value
        # can be overridden without changing application code or the URL.
        kwargs.update(
            {
                "pool_size": _env_int(
                    "NOVEL_DB_POOL_SIZE", "NOVEL_MYSQL_POOL_SIZE", default=10
                ),
                "max_overflow": _env_int(
                    "NOVEL_DB_MAX_OVERFLOW",
                    "NOVEL_MYSQL_MAX_OVERFLOW",
                    default=20,
                    minimum=0,
                ),
                "pool_timeout": _env_int(
                    "NOVEL_DB_POOL_TIMEOUT", "NOVEL_MYSQL_POOL_TIMEOUT", default=30
                ),
                "pool_recycle": _env_int(
                    "NOVEL_DB_POOL_RECYCLE", "NOVEL_MYSQL_POOL_RECYCLE", default=1800
                ),
            }
        )

    db_engine = create_engine(url, **kwargs)
    if db_engine.dialect.name == "sqlite":
        sqlite_wal_lock = threading.Lock()
        sqlite_wal_configured = False
        sqlite_file = not _sqlite_memory_url(url)

        @event.listens_for(db_engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            nonlocal sqlite_wal_configured
            cursor = dbapi_connection.cursor()
            try:
                # Configure the low-cost connection-local pragmas first.  In
                # particular, busy_timeout must be active before a competing
                # connection can observe the file while WAL is negotiated.
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA foreign_keys=ON")
                try:
                    if sqlite_file and not sqlite_wal_configured:
                        # NullPool creates short-lived connections, so doing a
                        # write-mode PRAGMA on every checkout can itself race
                        # with another opener.  Probe/set WAL once per engine
                        # and safely defer it when SQLite reports a transient
                        # lock.  Failures in the two pragmas above are not
                        # swallowed: a connection without those invariants is
                        # not safe for application work.
                        with sqlite_wal_lock:
                            if not sqlite_wal_configured:
                                cursor.execute("PRAGMA journal_mode")
                                mode = cursor.fetchone()
                                if not mode or str(mode[0]).lower() != "wal":
                                    cursor.execute("PRAGMA journal_mode=WAL")
                                sqlite_wal_configured = True
                except Exception as exc:
                    if sqlite_file:
                        logger.warning(
                            "SQLite WAL setup deferred",
                            extra={
                                "operation": "sqlite_wal_setup",
                                "error_type": type(exc).__name__,
                            },
                        )
            finally:
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
