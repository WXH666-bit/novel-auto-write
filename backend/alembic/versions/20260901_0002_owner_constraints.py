"""Enforce tenant owners and widen tenant-scoped upload paths.

Revision ID: 20260901_0002
Revises: 20260901_0001
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260901_0002"
down_revision = "20260901_0001"
branch_labels = None
depends_on = None


def _column(table: str, name: str) -> dict[str, object]:
    for column in sa.inspect(op.get_bind()).get_columns(table):
        if column["name"] == name:
            return column
    raise RuntimeError(f"{table}.{name} does not exist")


def _has_owner_foreign_key(table: str) -> bool:
    return any(
        foreign_key.get("referred_table") == "users"
        and foreign_key.get("constrained_columns") == ["owner_id"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table)
    )


def _enforce_owner(table: str, constraint_name: str) -> None:
    owner = _column(table, "owner_id")
    needs_nullable_change = bool(owner.get("nullable", True))
    needs_foreign_key = not _has_owner_foreign_key(table)
    if not needs_nullable_change and not needs_foreign_key:
        return

    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(table, recreate=recreate) as batch:
        if needs_nullable_change:
            batch.alter_column(
                "owner_id",
                existing_type=owner["type"],
                nullable=False,
            )
        if needs_foreign_key:
            batch.create_foreign_key(
                constraint_name,
                "users",
                ["owner_id"],
                ["id"],
                ondelete="CASCADE",
            )


def upgrade() -> None:
    # Revision 0001 has already assigned every legacy row to the disabled
    # legacy owner, so making these columns required cannot orphan old data.
    _enforce_owner("projects", "fk_projects_owner_id_users")
    _enforce_owner("provider_profiles", "fk_provider_profiles_owner_id_users")

    stored_name = _column("import_sources", "stored_name")
    length = getattr(stored_name["type"], "length", None)
    if length is None or int(length) < 255:
        with op.batch_alter_table(
            "import_sources",
            recreate="always" if op.get_bind().dialect.name == "sqlite" else "auto",
        ) as batch:
            batch.alter_column(
                "stored_name",
                existing_type=stored_name["type"],
                type_=sa.String(255),
                existing_nullable=False,
            )


def downgrade() -> None:
    raise RuntimeError("租户所有者约束不可安全原地降级；请恢复迁移前备份")

