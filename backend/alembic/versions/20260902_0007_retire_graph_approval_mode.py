"""Reserve the retired graph approval-mode revision.

Revision ID: 20260902_0007
Revises: 20260902_0006
Create Date: 2026-09-02

Some local databases applied an early version of this revision that added a
``projects.graph_approval_mode`` column.  The product no longer exposes that
setting.  Keeping the revision identifier preserves their Alembic history;
the following revision removes the obsolete column when it is present.
"""

from __future__ import annotations

revision = "20260902_0007"
down_revision = "20260902_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
