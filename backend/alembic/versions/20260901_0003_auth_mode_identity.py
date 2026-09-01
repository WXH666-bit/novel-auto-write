"""Allow deployment-selected username-only accounts.

Revision ID: 20260901_0003
Revises: 20260901_0002
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20260901_0003"
down_revision = "20260901_0002"
branch_labels = None
depends_on = None


def _columns() -> dict[str, dict[str, object]]:
    return {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns("users")}


def _indexes() -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("users")
        if index.get("name")
    }


def _checks() -> set[str]:
    return {
        check["name"]
        for check in sa.inspect(op.get_bind()).get_check_constraints("users")
        if check.get("name")
    }


def upgrade() -> None:
    # A new database may already contain these columns because revision 0001
    # creates the current metadata.  Existing installations need the guarded
    # additions below before the nullability change.
    columns = _columns()
    needs_check = "ck_users_identity_present" not in _checks()
    if needs_check:
        # A genuine 0002 table has no username column and makes email required.
        # Partially upgraded/custom tables may already have both identities,
        # so select only columns that actually exist before applying DDL.
        if "username_normalized" in columns:
            invalid_query = text(
                "SELECT COUNT(*) FROM users "
                "WHERE email_normalized IS NULL AND username_normalized IS NULL"
            )
        else:
            invalid_query = text(
                "SELECT COUNT(*) FROM users WHERE email_normalized IS NULL"
            )
        invalid = op.get_bind().execute(invalid_query).scalar_one()
        if invalid:
            raise RuntimeError("users 表中存在没有邮箱或用户名的账号，无法安全迁移")

    changes: list[sa.Column] = []
    if "username" not in columns:
        changes.append(sa.Column("username", sa.String(120), nullable=True))
    if "username_normalized" not in columns:
        changes.append(sa.Column("username_normalized", sa.String(120), nullable=True))

    email_needs_nullable = any(
        name in columns and bool(column.get("nullable", False)) is False
        for name, column in columns.items()
        if name in {"email", "email_normalized"}
    )
    if changes or email_needs_nullable:
        recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
        with op.batch_alter_table("users", recreate=recreate) as batch:
            for column in changes:
                batch.add_column(column)
            for name in ("email", "email_normalized"):
                column = columns.get(name)
                if column is not None and bool(column.get("nullable", False)) is False:
                    batch.alter_column(
                        name,
                        existing_type=column["type"],
                        existing_nullable=False,
                        nullable=True,
                    )
            if needs_check:
                batch.create_check_constraint(
                    "ck_users_identity_present",
                    "email_normalized IS NOT NULL OR username_normalized IS NOT NULL",
                )

    elif needs_check:
        with op.batch_alter_table(
            "users",
            recreate="always" if op.get_bind().dialect.name == "sqlite" else "auto",
        ) as batch:
            batch.create_check_constraint(
                "ck_users_identity_present",
                "email_normalized IS NOT NULL OR username_normalized IS NOT NULL",
            )

    if "ix_users_username_normalized" not in _indexes():
        op.create_index(
            "ix_users_username_normalized",
            "users",
            ["username_normalized"],
            unique=True,
        )

def downgrade() -> None:
    raise RuntimeError("认证身份字段不可安全原地降级；请恢复迁移前备份")
