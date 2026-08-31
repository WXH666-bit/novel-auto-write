"""Small compatibility helpers shared by workflow services.

The project intentionally keeps persistence code boring and explicit.  These
helpers make the services work with both SQLite JSON columns and older text
columns, and make unit tests with small SQLAlchemy models straightforward.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_RUN_STATUSES = {
    "queued",
    "preparing_context",
    "planning",
    "drafting",
    "extracting",
    "auditing",
    "revising",
    "awaiting_review",
    "committing",
    "needs_retry",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def model_columns(model: Any) -> set[str]:
    try:
        return set(model.__mapper__.attrs.keys())
    except AttributeError:
        try:
            return {column.name for column in model.__table__.columns}
        except AttributeError:
            return set()


def mapped_kwargs(model: Any, values: dict[str, Any]) -> dict[str, Any]:
    names = model_columns(model)
    return {key: value for key, value in values.items() if key in names}


def has_field(instance_or_model: Any, name: str) -> bool:
    model = instance_or_model if isinstance(instance_or_model, type) else type(instance_or_model)
    return name in model_columns(model)


def assign(instance: Any, name: str, value: Any) -> None:
    if has_field(instance, name):
        setattr(instance, name, value)


def json_for_model(model: Any, field: str, value: Any) -> Any:
    """Encode JSON for a Text column while preserving dicts for JSON columns."""

    try:
        column = model.__table__.columns.get(field)
        type_name = str(column.type).upper() if column is not None else ""
    except AttributeError:
        type_name = ""
    if any(name in type_name for name in ("CHAR", "TEXT", "CLOB", "VARCHAR")):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def read_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def token_estimate(text: str) -> int:
    # Chinese characters tend to be close to one token; ASCII prose is cheaper.
    chinese = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    ascii_count = len(text) - chinese
    return max(1, chinese + (ascii_count + 3) // 4)


def model_json_field(model: Any, *names: str) -> str | None:
    """Return the first mapped JSON-like field name from ``names``."""

    columns = model_columns(model)
    return next((name for name in names if name in columns), None)
