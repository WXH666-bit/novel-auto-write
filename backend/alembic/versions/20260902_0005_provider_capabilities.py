"""Canonicalize Provider capability flags.

This migration deliberately uses only Alembic/SQLAlchemy reflection and raw
rows.  It must be safe to run before the application model module is imported:
Provider JSON is data being normalized, not an invitation to instantiate ORM
objects or trigger model event handlers.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20260902_0005"
down_revision = "20260901_0004"
branch_labels = None
depends_on = None

_VISION_ALIASES = ("image_input", "supports_vision", "multimodal")
_TRUE_VALUES = frozenset(
    {"1", "true", "yes", "y", "on", "t", "enabled", "enable", "是", "开启"}
)
_FALSE_VALUES = frozenset(
    {"", "0", "false", "no", "n", "off", "f", "disabled", "disable", "否", "关闭"}
)


def _flag(value: Any) -> bool | None:
    """Parse a capability flag strictly, returning ``None`` for ambiguity."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    # Ambiguous legacy data is ignored so a later valid alias can still be
    # used.  The canonical fallback below remains fail-closed at ``False``.
    return None


def _canonical(value: Any) -> dict[str, Any]:
    parsed = value
    if isinstance(parsed, (bytes, bytearray)):
        parsed = parsed.decode("utf-8", errors="replace")
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (TypeError, json.JSONDecodeError):
            parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    result = dict(parsed)
    canonical_vision = _flag(parsed["vision"]) if "vision" in parsed else None
    if canonical_vision is not None:
        # A valid canonical value is authoritative, especially explicit false
        # which must not be re-enabled by stale aliases.
        vision = canonical_vision
    else:
        vision = False
        for key in _VISION_ALIASES:
            if key not in parsed:
                continue
            alias_vision = _flag(parsed[key])
            if alias_vision is not None:
                vision = alias_vision
                break
    for key in _VISION_ALIASES:
        result.pop(key, None)
    result["vision"] = vision
    return result


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "provider_profiles" not in inspector.get_table_names():
        return
    columns = {str(column["name"]) for column in inspector.get_columns("provider_profiles")}
    if "capabilities" not in columns:
        return

    # Materialize before issuing UPDATE statements.  Some MySQL DBAPI cursors
    # reject a second statement while the SELECT cursor is still streaming.
    rows = bind.execute(sa.text("SELECT id, capabilities FROM provider_profiles")).mappings().all()
    for row in rows:
        canonical = _canonical(row.get("capabilities"))
        bind.execute(
            sa.text(
                "UPDATE provider_profiles SET capabilities = :capabilities WHERE id = :id"
            ),
            {
                "id": row["id"],
                "capabilities": json.dumps(canonical, ensure_ascii=False, sort_keys=True),
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "provider_profiles" not in inspector.get_table_names():
        return
    columns = {str(column["name"]) for column in inspector.get_columns("provider_profiles")}
    if "capabilities" not in columns:
        return

    # The canonical object is intentionally kept alongside one legacy alias:
    # older application versions can read ``image_input`` while newer ones
    # still retain the authoritative ``vision`` flag.  Materialize first for
    # MySQL drivers that do not allow UPDATE while a SELECT cursor is open.
    rows = bind.execute(sa.text("SELECT id, capabilities FROM provider_profiles")).mappings().all()
    for row in rows:
        canonical = _canonical(row.get("capabilities"))
        canonical["image_input"] = canonical["vision"]
        bind.execute(
            sa.text(
                "UPDATE provider_profiles SET capabilities = :capabilities WHERE id = :id"
            ),
            {
                "id": row["id"],
                "capabilities": json.dumps(canonical, ensure_ascii=False, sort_keys=True),
            },
        )
