"""MySQL 8.4 integration coverage for migrations, ngram search, and tenancy.

The test database is deliberately controlled by ``NOVEL_TEST_MYSQL_URL``.  A
test run resets only the application's known tables so every test starts from
an isolated schema; do not point that variable at a database containing user
data.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import models
from backend.app.db import Base, create_engine_for_url, rebuild_search_index, run_migrations
from backend.app.services.search import search_accepted_chapters

MYSQL_URL = os.getenv("NOVEL_TEST_MYSQL_URL")

pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(
        not MYSQL_URL,
        reason="NOVEL_TEST_MYSQL_URL is not set",
    ),
]


@pytest.fixture(scope="module")
def mysql_engine() -> Iterator[Engine]:
    """Yield the explicitly configured integration-test engine."""

    assert MYSQL_URL
    engine = create_engine_for_url(MYSQL_URL)
    try:
        yield engine
    finally:
        engine.dispose()


def _reset_schema(engine: Engine) -> None:
    """Drop known application tables, including Alembic's version table.

    ``NOVEL_TEST_MYSQL_URL`` is an explicit disposable-database opt-in.  Only
    names from the application's declarative metadata are interpolated into
    the DDL; no user-provided value is used as an identifier.
    """

    known_tables = set(Base.metadata.tables) | {
        "alembic_version",
        # These are SQLite-only derived tables, but removing them makes a
        # reused test schema genuinely empty if an old setup left them behind.
        "chapter_fts",
        "canon_fts",
    }
    existing_tables = set(inspect(engine).get_table_names())
    tables_to_drop = sorted(existing_tables & known_tables)
    if not tables_to_drop:
        return

    with engine.begin() as connection:
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        try:
            for table in tables_to_drop:
                connection.exec_driver_sql(f"DROP TABLE IF EXISTS `{table}`")
        finally:
            connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")


@pytest.fixture()
def migrated_mysql(mysql_engine: Engine) -> Iterator[Engine]:
    """Reset and migrate a fresh schema for data-bearing integration tests."""

    _reset_schema(mysql_engine)
    run_migrations(mysql_engine)
    yield mysql_engine


@pytest.fixture()
def mysql_session(migrated_mysql: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=migrated_mysql, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _new_user(session: Session, email: str) -> models.User:
    user = models.User(
        email=email,
        email_normalized=email.casefold(),
        display_name=email.split("@", 1)[0],
        password_hash="integration-test-only",
        is_email_verified=True,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _new_project(
    session: Session,
    owner: models.User,
    name: str,
    content: str,
    *,
    accepted: bool = True,
) -> tuple[models.Project, models.Chapter]:
    project = models.Project(owner_id=owner.id, name=name)
    session.add(project)
    session.flush()
    chapter = models.Chapter(
        project_id=project.id,
        chapter_number=1,
        sort_order=1,
        title=f"{name}·第一章",
        status="confirmed" if accepted else "draft",
    )
    session.add(chapter)
    session.flush()
    revision = models.ChapterRevision(
        chapter_id=chapter.id,
        revision_number=1,
        content=content,
        content_hash=models.ChapterRevision.hash_content(content),
        source_type="manual",
    )
    session.add(revision)
    session.flush()
    chapter.current_revision_id = revision.id
    if accepted:
        chapter.accepted_revision_id = revision.id
    return project, chapter


def test_mysql_empty_database_migrates_to_head(mysql_engine: Engine) -> None:
    """A blank MySQL schema receives all tables and the ngram index."""

    _reset_schema(mysql_engine)
    assert inspect(mysql_engine).get_table_names() == []

    run_migrations(mysql_engine)

    inspector = inspect(mysql_engine)
    tables = set(inspector.get_table_names())
    assert {"alembic_version", "users", "projects", "search_documents"} <= tables
    with mysql_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "20260901_0003"
        )
    for table in ("projects", "provider_profiles"):
        owner = next(
            column for column in inspector.get_columns(table) if column["name"] == "owner_id"
        )
        assert owner["nullable"] is False
        assert any(
            foreign_key["constrained_columns"] == ["owner_id"]
            and foreign_key["referred_table"] == "users"
            for foreign_key in inspector.get_foreign_keys(table)
        )
    user_columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert user_columns["email"]["nullable"] is True
    assert user_columns["email_normalized"]["nullable"] is True
    assert {"username", "username_normalized"} <= set(user_columns)
    username_indexes = {
        index["name"]: index for index in inspector.get_indexes("users")
    }
    assert username_indexes["ix_users_username_normalized"]["unique"] is True
    assert "ck_users_identity_present" in {
        constraint["name"] for constraint in inspector.get_check_constraints("users")
    }
    indexes = {index["name"] for index in inspector.get_indexes("search_documents")}
    assert "ft_search_documents_ngram" in indexes


def test_mysql_existing_0002_users_upgrade_to_username_identity(mysql_engine: Engine) -> None:
    """The real 0002 shape is altered in place without losing email accounts."""

    _reset_schema(mysql_engine)
    with mysql_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE users (
                id VARCHAR(36) PRIMARY KEY,
                email VARCHAR(320) NOT NULL,
                email_normalized VARCHAR(320) NOT NULL,
                display_name VARCHAR(120),
                password_hash VARCHAR(512),
                is_email_verified BOOLEAN NOT NULL,
                is_active BOOLEAN NOT NULL,
                default_provider_id VARCHAR(36),
                failed_login_attempts INTEGER NOT NULL,
                locked_until DATETIME,
                last_login_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE KEY ix_users_email_normalized (email_normalized)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """
        )
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY) ENGINE=InnoDB"
        )
        connection.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('20260901_0002')"
        )
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id,email,email_normalized,password_hash,is_email_verified,is_active,"
            "failed_login_attempts,created_at,updated_at) VALUES "
            "('old-email','Old@Example.test','old@example.test','hash',1,1,0,NOW(),NOW())"
        )

    run_migrations(mysql_engine)
    inspector = inspect(mysql_engine)
    columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert columns["email"]["nullable"] is True
    assert columns["email_normalized"]["nullable"] is True
    assert {"username", "username_normalized"} <= set(columns)
    assert "ix_users_username_normalized" in {
        index["name"] for index in inspector.get_indexes("users")
    }
    assert "ck_users_identity_present" in {
        constraint["name"] for constraint in inspector.get_check_constraints("users")
    }
    with mysql_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "20260901_0003"
        )
        assert connection.execute(
            text("SELECT email_normalized FROM users WHERE id='old-email'")
        ).scalar_one() == "old@example.test"


def test_mysql_chinese_ngram_search_uses_only_accepted_content(
    mysql_session: Session,
    migrated_mysql: Engine,
) -> None:
    owner = _new_user(mysql_session, "ngram@example.test")
    accepted_project, accepted_chapter = _new_project(
        mysql_session,
        owner,
        "中文检索",
        "林渡在雾港灯塔下听见潮声。",
    )
    _new_project(
        mysql_session,
        owner,
        "未确认草稿",
        "灯塔里的草稿不应进入检索。",
        accepted=False,
    )
    mysql_session.commit()

    rebuild_search_index(db_engine=migrated_mysql)
    rows = search_accepted_chapters(
        mysql_session,
        owner_id=owner.id,
        project_id=accepted_project.id,
        query="灯塔",
    )

    assert [(row[0], row[1]) for row in rows] == [
        (accepted_chapter.id, accepted_chapter.accepted_revision_id)
    ]
    assert "灯塔" in rows[0][2]
    indexed = mysql_session.execute(
        text(
            "SELECT COUNT(*) FROM search_documents "
            "WHERE owner_id = :owner_id AND project_id = :project_id"
        ),
        {"owner_id": owner.id, "project_id": accepted_project.id},
    ).scalar_one()
    assert indexed == 1


def test_mysql_search_enforces_owner_and_project_boundaries(
    mysql_session: Session,
    migrated_mysql: Engine,
) -> None:
    owner_a = _new_user(mysql_session, "owner-a@example.test")
    owner_b = _new_user(mysql_session, "owner-b@example.test")
    project_a, chapter_a = _new_project(
        mysql_session,
        owner_a,
        "甲的灯塔",
        "甲在灯塔留下了红色信标。",
    )
    project_a_other, chapter_a_other = _new_project(
        mysql_session,
        owner_a,
        "甲的另一座灯塔",
        "甲在另一座灯塔留下了蓝色信标。",
    )
    project_b, chapter_b = _new_project(
        mysql_session,
        owner_b,
        "乙的灯塔",
        "乙在灯塔留下了黑色信标。",
    )
    mysql_session.commit()
    rebuild_search_index(db_engine=migrated_mysql)

    def search(owner_id: str, project_id: str) -> list[tuple[str, str, str]]:
        return search_accepted_chapters(
            mysql_session,
            owner_id=owner_id,
            project_id=project_id,
            query="灯塔",
        )

    assert [row[0] for row in search(owner_a.id, project_a.id)] == [chapter_a.id]
    assert [row[0] for row in search(owner_a.id, project_a_other.id)] == [chapter_a_other.id]
    assert [row[0] for row in search(owner_b.id, project_b.id)] == [chapter_b.id]
    assert search(owner_a.id, project_b.id) == []
    assert search(owner_b.id, project_a.id) == []
