"""Add derived memory, character cards, graph, media, and assistant state.

Revision ID: 20260901_0004
Revises: 20260901_0003
Create Date: 2026-09-01

The first migration creates the model metadata on a fresh database.  This
revision is therefore deliberately idempotent: it creates only the new tables
and adds the handful of columns needed by existing installations.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app import models  # noqa: F401 (register the complete metadata)
from app.db import Base

revision = "20260901_0004"
down_revision = "20260901_0003"
branch_labels = None
depends_on = None


def _columns(table: str) -> dict[str, dict[str, object]]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return {}
    return {column["name"]: column for column in inspector.get_columns(table)}


def _add_columns(table: str, columns: list[sa.Column]) -> None:
    # Some installations were created from a minimal 0002 database and do
    # not yet have every optional legacy table (notably ``jobs``).  A missing
    # table is materialised by its owning migration later; 0004 must remain
    # safe and additive instead of failing while trying to alter it.
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return
    existing = _columns(table)
    pending = [column for column in columns if column.name not in existing]
    if not pending:
        return
    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(table, recreate=recreate) as batch:
        for column in pending:
            batch.add_column(column)


def _json_server_default() -> sa.TextClause:
    """Return a JSON object default accepted by each supported backend."""

    # MySQL rejects a quoted string literal as a JSON DEFAULT.  An expression
    # default is supported by the MySQL versions used in production, while
    # SQLite needs the ordinary quoted JSON text.
    if op.get_bind().dialect.name == "mysql":
        return sa.text("(JSON_OBJECT())")
    return sa.text("'{}'")


def _unique_constraints(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {
        str(item.get("name"))
        for item in inspector.get_unique_constraints(table)
        if item.get("name")
    }


def _collapse_duplicate_story_summaries(bind: sa.engine.Connection) -> None:
    """Merge pre-migration MySQL duplicates before adding the new UNIQUE key."""

    duplicate_groups = bind.execute(
        sa.text(
            "SELECT project_id, scope_key, MIN(id) AS keep_id "
            "FROM story_summaries "
            "GROUP BY project_id, scope_key HAVING COUNT(*) > 1"
        )
    ).mappings().all()
    has_revisions = "story_summary_revisions" in sa.inspect(bind).get_table_names()
    for group in duplicate_groups:
        duplicate_ids = bind.execute(
            sa.text(
                "SELECT id FROM story_summaries "
                "WHERE project_id = :project_id AND scope_key = :scope_key AND id <> :keep_id"
            ),
            {
                "project_id": group["project_id"],
                "scope_key": group["scope_key"],
                "keep_id": group["keep_id"],
            },
        ).scalars().all()
        for duplicate_id in duplicate_ids:
            if has_revisions:
                bind.execute(
                    sa.text(
                        "UPDATE story_summary_revisions SET story_summary_id = :keep_id "
                        "WHERE story_summary_id = :duplicate_id"
                    ),
                    {"keep_id": group["keep_id"], "duplicate_id": duplicate_id},
                )
            bind.execute(
                sa.text("DELETE FROM story_summaries WHERE id = :duplicate_id"),
                {"duplicate_id": duplicate_id},
            )


def _migrate_story_summary_scope_key() -> None:
    """Make project-level summary uniqueness portable to MySQL.

    The old nullable ``chapter_id`` key permits duplicate project summaries
    on MySQL.  ``scope_key`` is populated before becoming NOT NULL and is the
    sole portable uniqueness key for both project and chapter summaries.
    """

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "story_summaries" not in inspector.get_table_names():
        return
    columns = _columns("story_summaries")
    if "scope_key" not in columns:
        with op.batch_alter_table(
            "story_summaries",
            recreate="always" if bind.dialect.name == "sqlite" else "auto",
        ) as batch:
            batch.add_column(sa.Column("scope_key", sa.String(300), nullable=True))
    suffix_expression = (
        "scope || ':' || chapter_id"
        if bind.dialect.name == "sqlite"
        else "CONCAT(scope, ':', chapter_id)"
    )
    bind.execute(
        sa.text(
            "UPDATE story_summaries SET scope_key = "
            f"CASE WHEN scope = 'project' OR chapter_id IS NULL THEN scope "
            f"ELSE {suffix_expression} END "
            "WHERE scope_key IS NULL"
        )
    )
    _collapse_duplicate_story_summaries(bind)
    columns = _columns("story_summaries")
    if columns.get("scope_key", {}).get("nullable", True):
        with op.batch_alter_table(
            "story_summaries",
            recreate="always" if bind.dialect.name == "sqlite" else "auto",
        ) as batch:
            batch.alter_column(
                "scope_key",
                existing_type=columns["scope_key"]["type"],
                existing_nullable=True,
                nullable=False,
            )
    constraints = _unique_constraints("story_summaries")
    if "uq_story_summary_project_scope_chapter" in constraints:
        with op.batch_alter_table(
            "story_summaries",
            recreate="always" if bind.dialect.name == "sqlite" else "auto",
        ) as batch:
            batch.drop_constraint("uq_story_summary_project_scope_chapter", type_="unique")
    if "uq_story_summary_project_scope_key" not in _unique_constraints("story_summaries"):
        with op.batch_alter_table(
            "story_summaries",
            recreate="always" if bind.dialect.name == "sqlite" else "auto",
        ) as batch:
            batch.create_unique_constraint(
                "uq_story_summary_project_scope_key", ["project_id", "scope_key"]
            )


def _new_tables() -> list[sa.Table]:
    names = (
        "story_summaries",
        "story_summary_revisions",
        "memory_build_runs",
        "memory_build_artifacts",
        "characters",
        "character_revisions",
        "change_sets",
        "proposals",
        "story_graph_nodes",
        "story_graph_edges",
        "story_graph_layouts",
        "media_assets",
        "agent_conversations",
        "agent_messages",
        "agent_runs",
        "agent_events",
        "agent_tool_calls",
    )
    return [Base.metadata.tables[name] for name in names]


def upgrade() -> None:
    # ``create_all`` honours foreign-key dependencies and checkfirst, making
    # this safe after 0001 has already materialised the latest metadata.
    Base.metadata.create_all(bind=op.get_bind(), tables=_new_tables(), checkfirst=True)
    _migrate_story_summary_scope_key()

    _add_columns(
        "users",
        [
            sa.Column("auto_summary_enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("preferences_version", sa.Integer(), nullable=False, server_default="1"),
        ],
    )
    _add_columns(
        "jobs",
        [
            sa.Column(
                "kind", sa.String(30), nullable=False, server_default="generation"
            ),
            sa.Column("resource_id", sa.String(36), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        ],
    )
    _add_columns(
        "review_bundles",
        [sa.Column("summary_candidate", sa.Text(), nullable=True)],
    )
    _add_columns(
        "review_bundles",
        [
            sa.Column(
                "structured_candidates",
                sa.JSON(),
                nullable=False,
                server_default=_json_server_default(),
            )
        ],
    )

    # Existing review rows receive the server default during the additive
    # column operation.  Keep a defensive backfill for partially upgraded
    # installations where the column was created nullable by an older build.
    bind = op.get_bind()
    if "structured_candidates" in _columns("review_bundles"):
        if bind.dialect.name == "mysql":
            bind.execute(
                sa.text(
                    "UPDATE review_bundles SET structured_candidates = JSON_OBJECT() "
                    "WHERE structured_candidates IS NULL"
                )
            )
        else:
            bind.execute(
                sa.text(
                    "UPDATE review_bundles SET structured_candidates = :empty "
                    "WHERE structured_candidates IS NULL"
                ),
                {"empty": "{}"},
            )


def downgrade() -> None:
    raise RuntimeError("故事工作区新实体不可安全原地降级；请恢复迁移前备份")
