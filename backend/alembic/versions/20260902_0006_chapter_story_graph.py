"""Scope story graphs and assistant proposals to a chapter.

Revision ID: 20260902_0006
Revises: 20260902_0005
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260902_0006"
down_revision = "20260902_0005"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def _unique_constraints(table: str) -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_unique_constraints(table)
        if item.get("name")
    }


def _indexes(table: str) -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_indexes(table)
        if item.get("name")
    }


def _add_scope_column(table: str) -> None:
    if table not in _tables() or "scope_chapter_id" in _columns(table):
        return
    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    ondelete = "SET NULL" if table == "proposals" else "CASCADE"
    unique_change = {
        "story_graph_nodes": (
            "uq_story_graph_node_ref",
            "uq_story_graph_node_chapter_ref",
            ["project_id", "scope_chapter_id", "node_type", "ref_id"],
        ),
        "story_graph_edges": (
            "uq_story_graph_edge_relation",
            "uq_story_graph_edge_chapter_relation",
            [
                "project_id",
                "scope_chapter_id",
                "source_node_id",
                "target_node_id",
                "relation_type",
            ],
        ),
        "story_graph_layouts": (
            "uq_story_graph_layout_project",
            "uq_story_graph_layout_project_chapter",
            ["project_id", "scope_chapter_id"],
        ),
    }.get(table)
    constraints = _unique_constraints(table)
    with op.batch_alter_table(table, recreate=recreate) as batch:
        batch.add_column(
            sa.Column(
                "scope_chapter_id",
                sa.String(36),
                sa.ForeignKey(
                    "chapters.id",
                    name=f"fk_{table}_scope_chapter_id",
                    ondelete=ondelete,
                ),
                nullable=True,
            )
        )
        # SQLite's batch recreation can lose the name of an existing unique
        # constraint. Replace it in this same rebuild while reflection still
        # knows the original name; a later batch could no longer drop it.
        if unique_change is not None:
            old_name, new_name, columns = unique_change
            if old_name in constraints:
                batch.drop_constraint(old_name, type_="unique")
            if new_name not in constraints:
                batch.create_unique_constraint(new_name, columns)


def _backfill() -> None:
    bind = op.get_bind()
    tables = _tables()
    if {"story_graph_nodes", "projects"}.issubset(tables):
        bind.execute(
            sa.text(
                "UPDATE story_graph_nodes SET scope_chapter_id = COALESCE("
                "scope_chapter_id, "
                "(SELECT current_chapter_id FROM projects "
                "WHERE projects.id = story_graph_nodes.project_id), chapter_id)"
            )
        )
    if {"story_graph_edges", "story_graph_nodes", "projects"}.issubset(tables):
        bind.execute(
            sa.text(
                "UPDATE story_graph_edges SET scope_chapter_id = COALESCE("
                "scope_chapter_id, "
                "(SELECT scope_chapter_id FROM story_graph_nodes "
                "WHERE story_graph_nodes.id = story_graph_edges.source_node_id), "
                "(SELECT current_chapter_id FROM projects "
                "WHERE projects.id = story_graph_edges.project_id))"
            )
        )
    for table in ("story_graph_layouts", "proposals"):
        if {table, "projects"}.issubset(tables):
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET scope_chapter_id = COALESCE("
                    "scope_chapter_id, "
                    f"(SELECT current_chapter_id FROM projects WHERE projects.id = {table}.project_id))"
                )
            )


def _replace_uniques() -> None:
    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    changes = (
        (
            "story_graph_nodes",
            "uq_story_graph_node_ref",
            "uq_story_graph_node_chapter_ref",
            ["project_id", "scope_chapter_id", "node_type", "ref_id"],
        ),
        (
            "story_graph_edges",
            "uq_story_graph_edge_relation",
            "uq_story_graph_edge_chapter_relation",
            [
                "project_id",
                "scope_chapter_id",
                "source_node_id",
                "target_node_id",
                "relation_type",
            ],
        ),
        (
            "story_graph_layouts",
            "uq_story_graph_layout_project",
            "uq_story_graph_layout_project_chapter",
            ["project_id", "scope_chapter_id"],
        ),
    )
    for table, old_name, new_name, columns in changes:
        if table not in _tables():
            continue
        constraints = _unique_constraints(table)
        if old_name not in constraints and new_name in constraints:
            continue
        with op.batch_alter_table(table, recreate=recreate) as batch:
            if old_name in constraints:
                batch.drop_constraint(old_name, type_="unique")
            if new_name not in constraints:
                batch.create_unique_constraint(new_name, columns)


def _add_indexes() -> None:
    desired = (
        (
            "story_graph_nodes",
            "ix_story_graph_nodes_project_chapter_type",
            ["project_id", "scope_chapter_id", "node_type"],
        ),
        (
            "story_graph_edges",
            "ix_story_graph_edges_project_chapter",
            ["project_id", "scope_chapter_id", "relation_type"],
        ),
        ("story_graph_layouts", "ix_story_graph_layouts_scope_chapter_id", ["scope_chapter_id"]),
        ("proposals", "ix_proposals_scope_chapter_id", ["scope_chapter_id"]),
    )
    for table, name, columns in desired:
        if table in _tables() and name not in _indexes(table):
            op.create_index(name, table, columns)


def upgrade() -> None:
    for table in (
        "story_graph_nodes",
        "story_graph_edges",
        "story_graph_layouts",
        "proposals",
    ):
        _add_scope_column(table)
    _backfill()
    _replace_uniques()
    _add_indexes()


def downgrade() -> None:
    # Chapter scope is semantic data. Keep the additive columns on downgrade
    # so an older application cannot silently merge different chapter graphs.
    pass
