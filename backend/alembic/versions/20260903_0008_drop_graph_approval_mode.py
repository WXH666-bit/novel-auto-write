"""Drop the retired per-project graph approval mode.

Revision ID: 20260903_0008
Revises: 20260902_0007
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260903_0008"
down_revision = "20260902_0007"
branch_labels = None
depends_on = None


def _project_columns() -> set[str] | None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "projects" not in inspector.get_table_names():
        return None
    return {
        str(column["name"])
        for column in inspector.get_columns("projects")
    }


def upgrade() -> None:
    columns = _project_columns()
    if columns is None or "graph_approval_mode" not in columns:
        return
    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("projects", recreate=recreate) as batch:
        batch.drop_column("graph_approval_mode")


def downgrade() -> None:
    columns = _project_columns()
    if columns is None or "graph_approval_mode" in columns:
        return
    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("projects", recreate=recreate) as batch:
        batch.add_column(
            sa.Column(
                "graph_approval_mode",
                sa.String(24),
                server_default="auto",
                nullable=False,
            )
        )
