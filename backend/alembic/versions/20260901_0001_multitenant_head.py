"""Create the current schema and upgrade the original single-user database.

Revision ID: 20260901_0001
Revises:
Create Date: 2026-09-01
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from app import models  # noqa: F401  (register all metadata)
from app.config import LEGACY_OWNER_ID
from app.db import Base

revision = "20260901_0001"
down_revision = None
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table)
        if index.get("name")
    }


def _add(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()

    # On a new database this creates the complete current schema.  On the
    # original SQLite database it creates only the new auth/search tables;
    # Alembic then adds the tenant/provider columns below.
    Base.metadata.create_all(bind=bind, checkfirst=True)

    _add("projects", sa.Column("owner_id", sa.String(36), nullable=True))
    _add("provider_profiles", sa.Column("owner_id", sa.String(36), nullable=True))
    _add("provider_profiles", sa.Column("api_version", sa.String(80), nullable=True))
    _add("provider_profiles", sa.Column("max_output_tokens", sa.Integer(), nullable=True))
    _add(
        "provider_profiles",
        sa.Column("anthropic_workspace_id", sa.String(255), nullable=True),
    )
    _add(
        "provider_profiles",
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
    )
    _add("provider_profiles", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    _add("audit_logs", sa.Column("actor_user_id", sa.String(36), nullable=True))
    _add("generation_runs", sa.Column("provider_profile_id", sa.String(36), nullable=True))
    _add("generation_runs", sa.Column("provider_protocol", sa.String(40), nullable=True))
    _add("generation_runs", sa.Column("provider_config_version", sa.Integer(), nullable=True))
    _add(
        "generation_runs",
        sa.Column(
            "provider_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )

    if "ix_projects_owner_id" not in _indexes("projects"):
        op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    if "ix_provider_profiles_owner_id" not in _indexes("provider_profiles"):
        op.create_index("ix_provider_profiles_owner_id", "provider_profiles", ["owner_id"])
    if "ix_generation_runs_provider_profile_id" not in _indexes("generation_runs"):
        op.create_index(
            "ix_generation_runs_provider_profile_id",
            "generation_runs",
            ["provider_profile_id"],
        )
    if "ix_audit_logs_actor_user_id" not in _indexes("audit_logs"):
        op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])

    # Quarantine old single-user rows.  This owner has no password, is
    # inactive, and can only be transferred through the operator CLI.
    existing = bind.execute(
        sa.text("SELECT id FROM users WHERE id = :id"), {"id": LEGACY_OWNER_ID}
    ).first()
    if existing is None:
        now = datetime.now(UTC)
        bind.execute(
            sa.text(
                "INSERT INTO users "
                "(id,email,email_normalized,display_name,password_hash,is_email_verified,"
                "is_active,failed_login_attempts,created_at,updated_at) "
                "VALUES (:id,:email,:normalized,:display,NULL,0,0,0,:created,:updated)"
            ),
            {
                "id": LEGACY_OWNER_ID,
                "email": "legacy-owner@invalid.local",
                "normalized": "legacy-owner@invalid.local",
                "display": "Legacy owner (disabled)",
                "created": now,
                "updated": now,
            },
        )
    bind.execute(
        sa.text("UPDATE projects SET owner_id = :owner WHERE owner_id IS NULL"),
        {"owner": LEGACY_OWNER_ID},
    )
    bind.execute(
        sa.text("UPDATE provider_profiles SET owner_id = :owner WHERE owner_id IS NULL"),
        {"owner": LEGACY_OWNER_ID},
    )

    if bind.dialect.name == "mysql":
        indexes = _indexes("search_documents")
        if "ft_search_documents_ngram" not in indexes:
            op.execute(
                "CREATE FULLTEXT INDEX ft_search_documents_ngram "
                "ON search_documents (title, content) WITH PARSER ngram"
            )


def downgrade() -> None:
    # Removing ownership/auth columns would silently merge tenants and is not
    # a safe automated operation.  Restore the pre-migration backup instead.
    raise RuntimeError("多租户迁移不可原地降级；请恢复迁移前备份")
