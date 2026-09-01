"""Schema-versioned project ZIP backup and restore helpers."""

from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import uuid
import zipfile
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DATA_DIR
from ..db import Base
from .common import mapped_kwargs, safe_text
from .importer import content_hash

EXPORT_SCHEMA_VERSION = "2.1"
SUPPORTED_EXPORT_MAJORS = {"1", EXPORT_SCHEMA_VERSION.split(".")[0]}
MAX_ARCHIVE_ENTRIES = 20_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_JSON_MEMBER_BYTES = 64 * 1024 * 1024

# The feature branch that introduces story memory, character cards, the graph,
# assets, and the assistant uses optional tables.  Keeping these names in one
# compatibility map lets an older installation continue exporting/restoring
# its original 2.0 rows while a 2.1 installation does not need this service to
# know about every future model at import time.
OPTIONAL_MEMBER_ALIASES: dict[str, tuple[str, ...]] = {
    "summaries": ("summaries", "story_summaries", "chapter_summaries", "memory_summaries"),
    "summary_revisions": (
        "summary_revisions",
        "story_summary_revisions",
        "chapter_summary_revisions",
        "memory_summary_revisions",
    ),
    "memory_build_runs": ("memory_build_runs", "memory_runs", "memory_builds"),
    "memory_build_artifacts": (
        "memory_build_artifacts",
        "memory_artifacts",
        "memory_checkpoints",
    ),
    "characters": ("characters", "character_cards", "character_profiles"),
    "character_revisions": (
        "character_revisions",
        "character_card_revisions",
        "character_profile_revisions",
    ),
    "graph_nodes": ("graph_nodes", "story_graph_nodes", "story_nodes"),
    "graph_edges": ("graph_edges", "story_graph_edges", "story_edges", "plot_edges"),
    "graph_layout": ("graph_layout", "graph_layouts", "story_graph_layouts", "story_map_layouts"),
    "assets": ("assets", "project_assets", "character_assets", "media_assets"),
    "assistant_conversations": (
        "assistant_conversations",
        "agent_conversations",
        "assistant_threads",
    ),
    "assistant_messages": ("assistant_messages", "agent_messages", "assistant_chat_messages"),
    "assistant_runs": ("assistant_runs", "agent_runs", "assistant_executions"),
    "assistant_events": ("assistant_events", "agent_events", "assistant_event_log"),
    "assistant_tool_calls": ("assistant_tool_calls", "agent_tool_calls", "assistant_tools"),
    "assistant_change_sets": ("assistant_change_sets", "change_sets", "agent_change_sets"),
    "assistant_proposals": (
        "assistant_proposals",
        "agent_proposals",
        "setting_proposals",
        "proposals",
    ),
}

_OPTIONAL_TABLES = {
    name
    for names in OPTIONAL_MEMBER_ALIASES.values()
    for name in names
}
_OPTIONAL_EXCLUDED_TABLES = {
    "users",
    "user_sessions",
    "email_tokens",
    "auth_rate_limits",
    "provider_profiles",
    "search_documents",
    "chapter_fts",
    "canon_fts",
    "generation_runs",
    "generation_artifacts",
    "jobs",
    "audit_logs",
    "import_sources",
}

_SECRET_FIELD_RE = re.compile(
    r"(?:^|_)(?:api[_-]?key|secret(?:[_-]?key)?|password|credential(?:s)?|authorization|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|bearer[_-]?token)(?:_|$)",
    re.IGNORECASE,
)
_ASSET_FIELD_NAMES = (
    "stored_name",
    "storage_key",
    "storage_path",
    "relative_path",
    "file_path",
    "filepath",
    "path",
    "location",
)
_ASSET_BYTES_FIELD_NAMES = ("bytes", "blob", "binary", "data")
_ASSET_FILENAME_FIELD_NAMES = ("filename", "file_name", "original_name", "name")
_ASSET_MIME_FIELD_NAMES = ("mime_type", "mime", "content_type", "media_type")
_ASSET_HASH_FIELD_NAMES = ("sha256", "checksum", "content_hash", "file_hash", "hash")
_ASSET_SIZE_FIELD_NAMES = ("byte_size", "size", "file_size")
_ASSET_ROOT_NAMES = ("assets", "character_assets", "media", "uploads")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


class _RestoreFileJournal:
    """Track files created by one restore for rollback-safe cleanup."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.created: list[Path] = []

    def record(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved == self.root or self.root not in resolved.parents:
            raise ValueError("恢复文件路径越过数据目录")
        if resolved not in self.created:
            self.created.append(resolved)

    def rollback(self) -> None:
        for path in reversed(self.created):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
        # Files are tenant-scoped, so removing now-empty leaf directories is
        # safe and avoids leaving a misleading half-restored tree behind.
        for path in sorted(self.created, key=lambda item: len(item.parts), reverse=True):
            parent = path.parent
            while parent != self.root and self.root in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent


def _write_restore_file(path: Path, raw: bytes, journal: _RestoreFileJournal) -> None:
    """Atomically materialise a new restore file and journal its destination."""

    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    journal.record(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _secret_field(name: str) -> bool:
    """Return whether a database field must never cross the export boundary."""

    normalized = str(name).strip().replace("-", "_").lower()
    return bool(_SECRET_FIELD_RE.search(normalized)) or normalized in {
        "api_key_ref",
        "apikey_ref",
        "api_key",
        "apikey",
        "secret_key",
        "credential_ref",
        "credentials",
        "auth_token",
        "bearer_token",
    }


def _jsonable(value: Any, *, field_name: str | None = None) -> Any:
    """Convert ORM/JSON values while recursively removing secret fields.

    Assistant proposals and conversation metadata are user-controlled JSON.
    Sanitising nested keys here protects a 2.1 archive even if a later model
    adds a credential-shaped field without updating this exporter.
    """

    if field_name and _secret_field(field_name):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item, field_name=str(key))
            for key, item in value.items()
            if not _secret_field(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Binary assets are written as ZIP members, never embedded in JSON.
        return None
    return value


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row(item: Any, *, exclude: set[str] | None = None) -> dict[str, Any]:
    exclude = exclude or set()
    try:
        names = item.__table__.columns.keys()
    except AttributeError:
        names = []
    return {
        name: _jsonable(getattr(item, name, None), field_name=name)
        for name in names
        if name not in exclude and not name.startswith("_") and not _secret_field(name)
    }


def _put_json(archive: zipfile.ZipFile, path: str, value: Any) -> None:
    archive.writestr(
        path,
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )


def _validate_archive(archive: zipfile.ZipFile) -> set[str]:
    """Reject malformed/oversized archives before reading any member."""

    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ValueError("项目备份包含过多文件")
    total = 0
    names: set[str] = set()
    for info in infos:
        total += int(info.file_size)
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError("项目备份解压后过大")
        if "\x00" in info.filename or "\\" in info.filename:
            raise ValueError("项目备份包含不安全路径")
        normalized = PurePath(info.filename)
        if (
            normalized.is_absolute()
            or ".." in normalized.parts
            or re.match(r"^[A-Za-z]:[\\/]", info.filename) is not None
        ):
            raise ValueError("项目备份包含不安全路径")
        names.add(info.filename)
    return names


def _read_json_member(archive: zipfile.ZipFile, name: str, *, default: Any = None) -> Any:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return default
    if info.file_size > MAX_JSON_MEMBER_BYTES:
        raise ValueError(f"项目备份中的 {name} 过大")
    try:
        return json.loads(archive.read(info))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"项目备份中的 {name} 不是有效 JSON") from exc


def _read_first_json_member(
    archive: zipfile.ZipFile,
    names: Iterable[str],
    *,
    default: Any = None,
) -> Any:
    """Read the first present alias, allowing 2.1 naming to evolve safely."""

    for name in names:
        if name in archive.namelist():
            return _read_json_member(archive, name, default=default)
    return default


def _model_registry() -> dict[str, Any]:
    """Return mapped classes by table name without importing optional models."""

    registry: dict[str, Any] = {}
    try:
        for mapper in Base.registry.mappers:
            table = getattr(mapper, "local_table", None)
            name = getattr(table, "name", None)
            if name:
                registry[str(name)] = mapper.class_
    except (AttributeError, RuntimeError):
        # Lightweight test doubles may not expose a SQLAlchemy registry.
        return {}
    return registry


def _optional_group_for_table(table_name: str) -> str | None:
    lowered = str(table_name).lower()
    if lowered in _OPTIONAL_EXCLUDED_TABLES:
        return None
    for group, aliases in OPTIONAL_MEMBER_ALIASES.items():
        if lowered in aliases:
            return group
    # Be liberal with plural/singular names used by feature branches.  The
    # exact aliases above remain preferred so unrelated tables are not swept
    # into a project archive by accident.
    if lowered.endswith("summary_revisions") or lowered.endswith("summary_revision"):
        return "summary_revisions"
    if lowered.endswith("summaries") or lowered.endswith("summary"):
        return "summaries"
    if lowered.endswith("memory_build_artifacts") or lowered.endswith("memory_artifacts"):
        return "memory_build_artifacts"
    if lowered.endswith("memory_build_runs") or lowered.endswith("memory_runs"):
        return "memory_build_runs"
    if lowered.endswith("character_revisions") or lowered.endswith("character_revision"):
        return "character_revisions"
    if lowered.endswith("characters") or lowered.endswith("character_cards"):
        return "characters"
    if lowered.endswith("graph_nodes") or lowered.endswith("story_nodes"):
        return "graph_nodes"
    if lowered.endswith("graph_edges") or lowered.endswith("story_edges"):
        return "graph_edges"
    if lowered.endswith("graph_layouts") or lowered.endswith("graph_layout"):
        return "graph_layout"
    if lowered.endswith("assets") or lowered.endswith("asset"):
        return "assets"
    if "assistant" in lowered or lowered.startswith("agent_"):
        if "tool" in lowered:
            return "assistant_tool_calls"
        if "event" in lowered:
            return "assistant_events"
        if "run" in lowered or "execution" in lowered:
            return "assistant_runs"
        if "proposal" in lowered:
            return "assistant_proposals"
        if "message" in lowered:
            return "assistant_messages"
        if "change_set" in lowered:
            return "assistant_change_sets"
        if "conversation" in lowered or "thread" in lowered:
            return "assistant_conversations"
    if lowered in {"proposals", "proposal"}:
        return "assistant_proposals"
    if lowered in {"change_sets", "change_set"}:
        return "assistant_change_sets"
    return None


def _optional_models() -> dict[str, list[tuple[str, Any]]]:
    """Group mapped optional models by the archive member they belong to."""

    grouped: dict[str, list[tuple[str, Any]]] = {key: [] for key in OPTIONAL_MEMBER_ALIASES}
    for table_name, model in sorted(_model_registry().items()):
        group = _optional_group_for_table(table_name)
        if group is not None:
            grouped.setdefault(group, []).append((table_name, model))
    return grouped


def _column_names(model: Any) -> set[str]:
    try:
        return set(model.__table__.columns.keys())
    except AttributeError:
        return set()


def _rows_for_project(
    session: Session,
    model: Any,
    project_id: str,
    *,
    owner_id: str | None = None,
) -> list[Any]:
    """Load rows directly scoped by project; child rows are collected later."""

    columns = _column_names(model)
    if "project_id" not in columns:
        return []
    column = getattr(model, "project_id", None)
    if column is None:
        return []
    conditions = [column == project_id]
    if owner_id is not None and "owner_id" in columns:
        owner_column = getattr(model, "owner_id", None)
        if owner_column is not None:
            conditions.append(owner_column == owner_id)
    return list(session.scalars(select(model).where(*conditions)).all())


def _rows_for_parent_ids(
    session: Session,
    model: Any,
    parent_ids: set[str],
    parent_fields: tuple[str, ...],
) -> list[Any]:
    if not parent_ids:
        return []
    columns = _column_names(model)
    for field in parent_fields:
        if field not in columns:
            continue
        column = getattr(model, field, None)
        if column is None:
            continue
        return list(session.scalars(select(model).where(column.in_(parent_ids))).all())
    return []


def _optional_rows(
    session: Session,
    project_id: str,
    *,
    owner_id: str | None = None,
) -> dict[str, list[Any]]:
    """Collect optional project data, including child tables without project_id."""

    grouped_models = _optional_models()
    result: dict[str, list[Any]] = {key: [] for key in OPTIONAL_MEMBER_ALIASES}
    seen: dict[str, set[str]] = {key: set() for key in OPTIONAL_MEMBER_ALIASES}

    def add(group: str, row: Any) -> None:
        row_id = str(getattr(row, "id", ""))
        marker = f"{row.__class__.__module__}:{row.__class__.__name__}:{row_id}"
        if marker not in seen[group]:
            seen[group].add(marker)
            result[group].append(row)

    for group, models in grouped_models.items():
        for _, model in models:
            for row in _rows_for_project(session, model, project_id, owner_id=owner_id):
                add(group, row)

    character_ids = {str(getattr(row, "id", "")) for row in result["characters"]}
    conversation_ids = {
        str(getattr(row, "id", "")) for row in result["assistant_conversations"]
    }
    summary_ids = {str(getattr(row, "id", "")) for row in result["summaries"]}
    message_ids = {str(getattr(row, "id", "")) for row in result["assistant_messages"]}
    change_set_ids = {
        str(getattr(row, "id", "")) for row in result["assistant_change_sets"]
    }

    child_specs: dict[str, tuple[set[str], tuple[str, ...]]] = {
        "character_revisions": (
            character_ids,
            ("character_id", "character_card_id"),
        ),
        "assistant_messages": (conversation_ids, ("conversation_id", "thread_id")),
        "assistant_runs": (conversation_ids, ("conversation_id", "thread_id")),
        "assistant_events": (conversation_ids, ("conversation_id", "thread_id")),
        "assistant_tool_calls": (conversation_ids, ("conversation_id", "thread_id")),
        "assistant_proposals": (
            conversation_ids | message_ids | change_set_ids,
            ("conversation_id", "thread_id", "message_id", "change_set_id"),
        ),
        "summary_revisions": (summary_ids, ("story_summary_id", "summary_id")),
        "memory_build_artifacts": (
            {str(getattr(row, "id", "")) for row in result["memory_build_runs"]},
            ("run_id", "memory_build_run_id"),
        ),
        "assets": (character_ids, ("character_id", "character_card_id")),
    }
    for group, (parent_ids, fields) in child_specs.items():
        for _, model in grouped_models.get(group, []):
            for row in _rows_for_parent_ids(session, model, parent_ids, fields):
                add(group, row)

    for rows in result.values():
        rows.sort(key=lambda row: (safe_text(getattr(row, "created_at", "")), str(getattr(row, "id", ""))))
    return result


def _safe_filename(value: Any, fallback: str = "asset.bin") -> str:
    name = PurePath(str(value or "")).name.strip()
    name = _SAFE_FILENAME_RE.sub("_", name).strip("._")
    return (name[:180] or fallback)[:180]


def _asset_value(item: Any, fields: tuple[str, ...]) -> Any:
    for field in fields:
        value = getattr(item, field, None)
        if value not in (None, ""):
            return value
    return None


def _under(root: Path, candidate: Path) -> Path | None:
    try:
        root = root.resolve()
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved == root or root not in resolved.parents:
        return None
    return resolved


def _asset_file(item: Any) -> Path | None:
    """Resolve an asset path only inside the configured tenant data directory."""

    raw_paths: list[str] = []
    for field in _ASSET_FIELD_NAMES:
        value = getattr(item, field, None)
        if isinstance(value, str) and value.strip() and not value.startswith(("http://", "https://")):
            raw_paths.append(value)
    roots = [(DATA_DIR / name).resolve() for name in _ASSET_ROOT_NAMES]
    roots.append(DATA_DIR.resolve())
    for raw in raw_paths:
        candidate = Path(raw)
        candidates = [candidate] if candidate.is_absolute() else [root / raw for root in roots]
        for possible in candidates:
            for root in roots:
                checked = _under(root, possible)
                if checked is not None and checked.is_file():
                    return checked
    return None


def _asset_bytes(item: Any) -> bytes | None:
    path = _asset_file(item)
    if path is not None:
        return path.read_bytes()
    for field in _ASSET_BYTES_FIELD_NAMES:
        value = getattr(item, field, None)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
    return None


def _asset_manifest(rows: list[Any]) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    manifest: list[dict[str, Any]] = []
    payloads: dict[str, bytes] = {}
    used_names: set[str] = set()
    for item in rows:
        asset_id = str(getattr(item, "id", ""))
        if not asset_id:
            continue
        filename = _safe_filename(_asset_value(item, _ASSET_FILENAME_FIELD_NAMES), "asset.bin")
        mime = _asset_value(item, _ASSET_MIME_FIELD_NAMES) or mimetypes.guess_type(filename)[0]
        raw = _asset_bytes(item)
        if raw is None:
            # Keep the metadata visible in the backup even when an operator
            # moved or lost the sidecar file.  Restore will skip this row,
            # rather than recreating a dangling path in the destination.
            manifest.append(
                {
                    "asset_id": asset_id,
                    "archive_name": None,
                    "sha256": _asset_value(item, _ASSET_HASH_FIELD_NAMES),
                    "byte_size": _asset_value(item, _ASSET_SIZE_FIELD_NAMES),
                    "filename": filename,
                    "mime_type": str(mime) if mime else None,
                    "missing": True,
                    "missing_reason": "file_not_found",
                }
            )
            continue
        digest = content_hash(raw)
        safe_asset_id = _safe_filename(asset_id, "asset")
        archive_name = f"assets/{safe_asset_id}-{filename}"
        if archive_name in used_names:
            archive_name = f"assets/{safe_asset_id}-{digest[:12]}-{filename}"
        used_names.add(archive_name)
        manifest.append(
            {
                "asset_id": asset_id,
                "archive_name": archive_name,
                "sha256": digest,
                "byte_size": len(raw),
                "filename": filename,
                "mime_type": str(mime) if mime else None,
                "missing": False,
            }
        )
        payloads[archive_name] = raw
    return manifest, payloads


_TRANSIENT_ASSISTANT_EVENT_TYPES = {
    "message_delta",
    "assistant_message_delta",
    "content_delta",
    "text_delta",
    "response_output_text_delta",
    "stream_chunk",
    "assistant_stream_chunk",
    "stream_fragment",
    "token_delta",
}


def _is_transient_assistant_event(row: Any) -> bool:
    """Identify transport-only stream fragments excluded from backups."""

    event_type = str(getattr(row, "event_type", "") or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", event_type).strip("_")
    if normalized in _TRANSIENT_ASSISTANT_EVENT_TYPES:
        return True
    # Provider event names vary (for example ``response.output_text.delta``),
    # but durable status/proposal events do not contain these fragment markers.
    if "delta" in normalized or "chunk" in normalized:
        return True
    payload = getattr(row, "payload_json", None)
    if isinstance(payload, Mapping):
        payload_type = str(payload.get("type") or payload.get("event_type") or "")
        normalized_payload = re.sub(r"[^a-z0-9]+", "_", payload_type.lower()).strip("_")
        return bool(
            "delta" in payload
            or "chunk" in payload
            or normalized_payload in _TRANSIENT_ASSISTANT_EVENT_TYPES
            or (
                "delta" in normalized_payload or "chunk" in normalized_payload
            )
        )
    return False


def _original_import_manifest(rows: list[Any]) -> list[dict[str, Any]]:
    """Describe imported source files, including sidecars missing on disk."""

    upload_root = (DATA_DIR / "uploads").resolve()
    manifest: list[dict[str, Any]] = []
    for source in rows:
        source_id = str(getattr(source, "id", "") or "")
        filename = PurePath(str(getattr(source, "filename", "") or "未命名稿件.txt")).name
        safe_name = _safe_filename(filename, "source.txt")
        stored_name = str(getattr(source, "stored_name", "") or "")
        candidate = (upload_root / stored_name).resolve()
        available = bool(
            stored_name
            and upload_root in candidate.parents
            and candidate.is_file()
        )
        manifest.append(
            {
                "source_id": source_id,
                "archive_name": f"original_imports/{source_id}-{safe_name}" if available else None,
                "filename": filename,
                "sha256": getattr(source, "source_hash", None),
                "byte_size": getattr(source, "byte_size", None),
                "missing": not available,
                "missing_reason": None if available else "file_not_found",
            }
        )
    return manifest


def _model_for_row(group: str, row: Mapping[str, Any], models: dict[str, list[tuple[str, Any]]]) -> Any | None:
    table_name = str(row.get("__table__") or "")
    if table_name:
        for candidate_name, model in models.get(group, []):
            if candidate_name == table_name:
                return model
    candidates = models.get(group, [])
    return candidates[0][1] if candidates else None


def _model_has_column(model: Any, field: str) -> bool:
    return field in _column_names(model)


def _new_model_id(model: Any) -> str | None:
    """Generate a UUID for the feature models, which use string identifiers."""

    try:
        column = model.__table__.c.get("id")
        python_type = column.type.python_type if column is not None else None
    except (AttributeError, NotImplementedError):
        python_type = None
    return str(uuid.uuid4()) if python_type in (None, str) and _model_has_column(model, "id") else None


def _map_id(
    value: Any,
    key: str,
    row: Mapping[str, Any],
    maps: Mapping[str, Mapping[str, Any]],
) -> Any:
    if value is None:
        return None
    text = str(value)
    normalized = str(key).lower()
    if normalized == "project_id":
        project = maps.get("project", {}).get(text)
        return getattr(project, "id", project) if project is not None else value
    direct_groups: dict[str, tuple[str, ...]] = {
        "chapter_id": ("chapter",),
        "source_chapter_id": ("chapter",),
        "character_id": ("characters",),
        "character_card_id": ("characters",),
        "character_revision_id": ("character_revisions",),
        "story_summary_id": ("summaries",),
        "summary_id": ("summaries",),
        "conversation_id": ("assistant_conversations",),
        "thread_id": ("assistant_conversations",),
        "message_id": ("assistant_messages",),
        "run_id": ("assistant_runs", "memory_build_runs"),
        "memory_build_run_id": ("memory_build_runs",),
        "memory_run_id": ("memory_build_runs",),
        "memory_build_artifact_id": ("memory_build_artifacts",),
        "event_id": ("assistant_events",),
        "tool_call_id": ("assistant_tool_calls",),
        "change_set_id": ("assistant_change_sets",),
        "proposal_id": ("assistant_proposals",),
        "source_node_id": ("graph_nodes",),
        "target_node_id": ("graph_nodes",),
        "node_id": ("graph_nodes",),
        "edge_id": ("graph_edges",),
        "layout_id": ("graph_layout",),
        "asset_id": ("assets",),
        "image_media_id": ("assets",),
        "image_id": ("assets",),
        "avatar_asset_id": ("assets",),
        "portrait_asset_id": ("assets",),
        "revision_id": ("revision", "character_revisions"),
        "source_revision_id": ("revision",),
        "draft_revision_id": ("revision",),
        "parent_revision_id": ("revision", "character_revisions"),
        "accepted_revision_id": ("revision",),
        "current_revision_id": ("revision", "character_revisions", "summary_revisions"),
        "superseded_by_id": ("canon",),
        "plot_thread_id": ("plot_thread",),
    }
    groups = direct_groups.get(normalized)
    if groups:
        for group in groups:
            mapped = maps.get(group, {}).get(text)
            if mapped is not None:
                return getattr(mapped, "id", mapped)

    if normalized in {"from_id", "to_id", "source_id", "target_id", "node_id"}:
        endpoint = normalized.removesuffix("_id")
        kind = str(row.get(f"{endpoint}_type") or row.get(f"{endpoint}_kind") or "").lower()
        type_groups = {
            "character": "characters",
            "person": "characters",
            "character_card": "characters",
            "chapter": "chapter",
            "plot": "plot_thread",
            "plot_thread": "plot_thread",
            "thread": "plot_thread",
            "timeline": "timeline_event",
            "timeline_event": "timeline_event",
            "event": "timeline_event",
            "asset": "assets",
        }
        group = type_groups.get(kind)
        if group:
            mapped = maps.get(group, {}).get(text)
            if mapped is not None:
                return getattr(mapped, "id", mapped)

    # Last-resort UUID remapping covers custom JSON-backed references while
    # leaving ordinary prose values untouched.
    for mapping in maps.values():
        mapped = mapping.get(text)
        if mapped is not None:
            return getattr(mapped, "id", mapped)
    return value


def _remap_nested(value: Any, key: str, row: Mapping[str, Any], maps: Mapping[str, Mapping[str, Any]]) -> Any:
    if isinstance(value, dict):
        remapped: dict[str, Any] = {}
        for child_key, child_value in value.items():
            child_key_text = str(child_key)
            if _secret_field(child_key_text):
                continue
            # Layout JSON commonly uses node IDs as object keys.  Remap only
            # exact IDs known to one of the restore maps; ordinary labels and
            # arbitrary user JSON keys remain untouched.
            mapped_key = child_key_text
            for mapping in maps.values():
                mapped = mapping.get(child_key_text)
                if mapped is not None:
                    mapped_key = str(getattr(mapped, "id", mapped))
                    break
            remapped[mapped_key] = _remap_nested(child_value, child_key_text, row, maps)
        return remapped
    if isinstance(value, list):
        return [_remap_nested(item, key, row, maps) for item in value]
    if isinstance(value, str):
        return _map_id(value, key, row, maps)
    return value


def _optional_values(
    model: Any,
    row: Mapping[str, Any],
    maps: Mapping[str, Mapping[str, Any]],
    *,
    owner_id: str,
    project_id: str,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in row.items():
        if key == "__table__" or key == "id" or key in {"created_at", "updated_at"}:
            continue
        if _secret_field(key):
            continue
        if key == "owner_id":
            value = owner_id
        elif key == "project_id":
            value = project_id
        elif key in {"created_by_user_id", "user_id", "owner_user_id", "author_user_id"}:
            # User rows and provider profiles are deliberately not portable.
            # Any author/creator reference in a feature row therefore belongs
            # to the account performing the restore.
            value = owner_id
        elif key == "provider_profile_id":
            # The source account's provider profile is intentionally excluded
            # from backups; leaving its old ID would create a dangling or
            # cross-account reference after restore.
            value = None
        elif key == "resource_id":
            # Memory build resource IDs point at local jobs, which are not
            # portable backup data and are deliberately excluded below.
            value = None
        elif key == "image_media_id":
            # A missing manifest entry must not leave a character pointing at
            # the source project's media ID after restore.
            mapped = _map_id(value, key, row, maps)
            value = mapped if str(value) in maps.get("assets", {}) else None
        elif key == "ref_id":
            node_type = str(row.get("node_type") or "").lower()
            type_groups = {
                "character": "characters",
                "person": "characters",
                "chapter": "chapter",
                "plot": "plot_thread",
                "plot_thread": "plot_thread",
                "timeline": "timeline_event",
                "timeline_event": "timeline_event",
                "event": "timeline_event",
            }
            group = type_groups.get(node_type)
            mapped = maps.get(group, {}).get(str(value)) if group else None
            value = getattr(mapped, "id", mapped) if mapped is not None else value
        elif key.endswith("_id"):
            value = _map_id(value, key, row, maps)
        elif isinstance(value, (dict, list)):
            value = _remap_nested(value, key, row, maps)
        values[key] = value
    if _model_has_column(model, "owner_id"):
        values["owner_id"] = owner_id
    if _model_has_column(model, "project_id"):
        values["project_id"] = project_id
    if _model_has_column(model, "scope_key") and _model_has_column(model, "scope"):
        scope = values.get("scope", row.get("scope", "project"))
        chapter = values.get("chapter_id", row.get("chapter_id"))
        # Keep the key tied to the remapped chapter ID, not the source archive
        # ID.  This also upgrades a pre-2.1 summary row that had no key.
        normalized_scope = str(scope or "project").strip().lower() or "project"
        values["scope_key"] = (
            normalized_scope
            if normalized_scope == "project" or chapter is None
            else f"{normalized_scope}:{chapter}"
        )
    return mapped_kwargs(model, values)


def _restore_optional_group(
    session: Session,
    rows: list[Any],
    group: str,
    models: dict[str, list[tuple[str, Any]]],
    maps: dict[str, dict[str, Any]],
    *,
    owner_id: str,
    project_id: str,
) -> list[Any]:
    restored: list[Any] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        model = _model_for_row(group, raw, models)
        if model is None:
            continue
        values = _optional_values(model, raw, maps, owner_id=owner_id, project_id=project_id)
        new_id = _new_model_id(model)
        if new_id is not None:
            values["id"] = new_id
        item = model(**values)
        session.add(item)
        session.flush()
        old_id = str(raw.get("id") or "")
        if old_id:
            maps.setdefault(group, {})[old_id] = item
        restored.append(item)
    return restored


def _remap_restored_links(
    rows_by_group: Mapping[str, list[Any]], maps: dict[str, dict[str, Any]]
) -> None:
    """Fix references whose target group is restored later in the dependency order.

    Most feature rows are inserted after their parent, but append-only revision
    pointers and assistant run/message links can point forward.  Replaying the
    raw value through the same allow-listed mapper after every group exists
    keeps those references inside the newly restored project.
    """

    for group, rows in rows_by_group.items():
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            old_id = str(raw.get("id") or "")
            item = maps.get(group, {}).get(old_id)
            if item is None:
                continue
            for key, value in raw.items():
                if key in {"id", "__table__", "project_id", "owner_id"}:
                    continue
                if key in {"created_by_user_id", "user_id", "owner_user_id", "author_user_id"}:
                    continue
                if key == "provider_profile_id":
                    continue
                if key == "source_id":
                    source_type = str(raw.get("source_type") or "").lower()
                    source_group = (
                        "assistant_runs"
                        if "run" in source_type
                        else "assistant_conversations"
                        if source_type in {"assistant", "agent", "conversation", "chat"}
                        else None
                    )
                    mapped = (
                        getattr(maps.get(source_group, {}).get(str(value)), "id", None)
                        if source_group
                        else None
                    )
                    if mapped is not None and hasattr(item, key):
                        setattr(item, key, mapped)
                    continue
                if key.endswith("_id"):
                    mapped = _map_id(value, key, raw, maps)
                elif isinstance(value, (dict, list)):
                    mapped = _remap_nested(value, key, raw, maps)
                else:
                    continue
                if mapped != value and hasattr(item, key):
                    setattr(item, key, mapped)


def _restore_asset_bytes(
    archive: zipfile.ZipFile,
    names: set[str],
    entries: list[Any],
    *,
    owner_id: str,
    project_id: str,
    journal: _RestoreFileJournal,
) -> dict[str, Path]:
    """Validate and materialise asset bytes under a tenant/project directory."""

    # New media is served from the formal uploads tree.  Keep the ``assets``
    # leaf for compatibility with the media service's storage-key contract,
    # but never write restored files back to the legacy DATA_DIR/assets root.
    root = (DATA_DIR / "uploads").resolve()
    project_root = (root / str(owner_id) / str(project_id) / "assets").resolve()
    if root not in project_root.parents:
        raise ValueError("人物图片存储路径无效")
    restored: dict[str, Path] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        asset_id = str(entry.get("asset_id") or "")
        archive_name = str(entry.get("archive_name") or "")
        if not asset_id or bool(entry.get("missing")):
            continue
        if not archive_name or archive_name not in names:
            continue
        if not archive_name.startswith("assets/"):
            raise ValueError("项目备份中的人物图片路径无效")
        raw = archive.read(archive_name)
        actual_hash = content_hash(raw)
        declared_hash = str(entry.get("sha256") or actual_hash)
        if declared_hash != actual_hash:
            raise ValueError("人物图片哈希校验失败")
        declared_size = entry.get("byte_size")
        if declared_size is not None and int(declared_size) != len(raw):
            raise ValueError("人物图片大小校验失败")
        suffix = PurePath(_safe_filename(entry.get("filename"), "asset.bin")).suffix
        destination = (project_root / f"{actual_hash}{suffix}").resolve()
        if destination.parent != project_root:
            raise ValueError("人物图片存储路径无效")
        if destination.exists():
            if not destination.is_file() or destination.read_bytes() != raw:
                destination = (
                    project_root / f"{actual_hash}-{uuid.uuid4().hex[:8]}{suffix}"
                ).resolve()
                if destination.exists() and (
                    not destination.is_file() or destination.read_bytes() != raw
                ):
                    raise ValueError("人物图片目标文件冲突")
        if not destination.exists():
            _write_restore_file(destination, raw, journal)
        restored[asset_id] = destination
    return restored


def _set_asset_storage_fields(model: Any, values: dict[str, Any], path: Path, *, owner_id: str, project_id: str) -> None:
    """Populate whichever path field an optional asset model exposes."""

    relative = str(path.relative_to(DATA_DIR.resolve())).replace("\\", "/")
    for field in _ASSET_FIELD_NAMES:
        if _model_has_column(model, field):
            values[field] = relative
            break
    for field in _ASSET_HASH_FIELD_NAMES:
        if _model_has_column(model, field) and not values.get(field):
            values[field] = content_hash(path.read_bytes())
            break
    for field in _ASSET_SIZE_FIELD_NAMES:
        if _model_has_column(model, field) and not values.get(field):
            values[field] = path.stat().st_size
            break


def _restore_optional_data(
    session: Session,
    archive: zipfile.ZipFile,
    names: set[str],
    *,
    owner_id: str,
    project_id: str,
    maps: dict[str, dict[str, Any]],
    journal: _RestoreFileJournal,
) -> None:
    """Restore 2.1 feature rows when their optional mappers are installed.

    A 2.0 archive simply has no members below, so this function becomes a
    no-op.  Rows are inserted in dependency order and old identifiers are
    retained in in-memory maps only; no foreign project identifier can leak
    into the restored rows.
    """

    models = _optional_models()
    if not any(models.values()):
        return

    rows_by_group: dict[str, list[Any]] = {}
    for group, aliases in OPTIONAL_MEMBER_ALIASES.items():
        member_names = tuple(f"{alias}.json" for alias in aliases)
        rows = _read_first_json_member(archive, member_names, default=[])
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise ValueError(f"项目备份中的 {member_names[0]} 格式错误")
        rows_by_group[group] = rows

    asset_entries = _read_first_json_member(
        archive,
        ("assets_manifest.json", "assets-manifest.json"),
        default=[],
    )
    if asset_entries is None:
        asset_entries = []
    if not isinstance(asset_entries, list):
        raise ValueError("项目备份中的 assets_manifest.json 格式错误")
    asset_paths = (
        _restore_asset_bytes(
            archive,
            names,
            asset_entries,
            owner_id=owner_id,
            project_id=project_id,
            journal=journal,
        )
        if models.get("assets")
        else {}
    )

    # Asset rows are restored first because character cards commonly point at
    # a portrait/avatar asset.  Rows with no validated bytes are skipped so a
    # stale external path cannot be reintroduced into a private project.
    for raw in rows_by_group.get("assets", []):
        if not isinstance(raw, Mapping):
            continue
        old_id = str(raw.get("id") or "")
        path = asset_paths.get(old_id)
        if path is None:
            continue
        model = _model_for_row("assets", raw, models)
        if model is None:
            continue
        values = _optional_values(model, raw, maps, owner_id=owner_id, project_id=project_id)
        for field in _ASSET_FIELD_NAMES:
            values.pop(field, None)
        _set_asset_storage_fields(model, values, path, owner_id=owner_id, project_id=project_id)
        new_id = _new_model_id(model)
        if new_id is not None:
            values["id"] = new_id
        item = model(**values)
        session.add(item)
        session.flush()
        if old_id:
            maps.setdefault("assets", {})[old_id] = item

    # Stream deltas are transient transport fragments.  Even if an older
    # archive contains them, do not make them durable again during restore.
    rows_by_group["assistant_events"] = [
        row
        for row in rows_by_group.get("assistant_events", [])
        if not _is_transient_assistant_event(row)
    ]

    for group in (
        "summaries",
        "summary_revisions",
        "memory_build_runs",
        "memory_build_artifacts",
        "characters",
        "character_revisions",
        "graph_nodes",
        "graph_edges",
        "graph_layout",
        "assistant_conversations",
        "assistant_runs",
        "assistant_messages",
        "assistant_events",
        "assistant_tool_calls",
        "assistant_change_sets",
        "assistant_proposals",
    ):
        _restore_optional_group(
            session,
            rows_by_group.get(group, []),
            group,
            models,
            maps,
            owner_id=owner_id,
            project_id=project_id,
        )
    _remap_restored_links(rows_by_group, maps)


def export_project_zip(session: Session, project_id: str, *, owner_id: str) -> bytes:
    from ..models import (
        CanonItem,
        Chapter,
        ChapterRevision,
        ImportSource,
        PlotThread,
        Project,
        ReviewBundle,
        TimelineEvent,
    )

    project = session.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
    )
    if project is None:
        raise LookupError("项目不存在")
    chapters = session.scalars(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order, Chapter.chapter_number)
    ).all()
    revisions = session.scalars(
        select(ChapterRevision)
        .join(Chapter, Chapter.id == ChapterRevision.chapter_id)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.id, ChapterRevision.revision_number)
    ).all()
    canon = session.scalars(
        select(CanonItem)
        .where(CanonItem.project_id == project_id)
        .order_by(CanonItem.canon_version, CanonItem.created_at)
    ).all()
    timeline = session.scalars(
        select(TimelineEvent)
        .where(TimelineEvent.project_id == project_id)
        .order_by(TimelineEvent.sequence)
    ).all()
    threads = session.scalars(
        select(PlotThread)
        .where(PlotThread.project_id == project_id)
        .order_by(PlotThread.created_at)
    ).all()
    bundles = session.scalars(
        select(ReviewBundle)
        .where(ReviewBundle.project_id == project_id)
        .order_by(ReviewBundle.created_at)
    ).all()
    import_sources = session.scalars(
        select(ImportSource)
        .where(ImportSource.project_id == project_id)
        .order_by(ImportSource.created_at)
    ).all()
    optional = _optional_rows(session, project_id, owner_id=owner_id)
    # ``message_delta`` rows are useful for live SSE replay but are transient
    # transport fragments, not portable story state.
    optional["assistant_events"] = [
        row
        for row in optional["assistant_events"]
        if not _is_transient_assistant_event(row)
    ]
    optional_rows: dict[str, list[dict[str, Any]]] = {}
    for group, rows in optional.items():
        encoded: list[dict[str, Any]] = []
        for item in rows:
            row = _row(item, exclude={"owner_id"})
            # Multiple optional classes may share an archive group.  Retain a
            # non-authoritative table hint so restore can choose the matching
            # mapper; it is removed before constructing the ORM object.
            table_name = getattr(getattr(item, "__table__", None), "name", None)
            if table_name:
                row["__table__"] = str(table_name)
            encoded.append(row)
        optional_rows[group] = encoded
    asset_manifest, asset_payloads = _asset_manifest(optional["assets"])
    import_manifest = _original_import_manifest(import_sources)
    present_imports = [item for item in import_manifest if not item["missing"]]
    upload_root = (DATA_DIR / "uploads").resolve()
    import_paths = {
        str(getattr(source, "id", "")): (upload_root / str(source.stored_name)).resolve()
        for source in import_sources
        if getattr(source, "stored_name", None)
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "format": "novel-auto-write-project",
            "project_id": str(project.id),
            "created_at": datetime.now().isoformat(),
            "contains": [
                "project.json",
                "chapters.json",
                "revisions.json",
                "canon.json",
                "timeline.json",
                "plot_threads.json",
                "review_bundles.json",
                "import_sources.json",
                "summaries.json",
                "summary_revisions.json",
                "memory_build_runs.json",
                "memory_build_artifacts.json",
                "characters.json",
                "character_revisions.json",
                "graph_nodes.json",
                "graph_edges.json",
                "graph_layout.json",
                "assets.json",
                "assets_manifest.json",
                "assets/",
                "original_imports_manifest.json",
                "assistant_conversations.json",
                "assistant_messages.json",
                "assistant_runs.json",
                "assistant_events.json",
                "assistant_tool_calls.json",
                "assistant_change_sets.json",
                "assistant_proposals.json",
                "chapters/",
                "original_imports/",
            ],
            # Provider profiles (and therefore their credentials) are never
            # exported.  Avoid writing a credential field name into the archive
            # as well, so a simple backup scan cannot mistake metadata for a
            # leaked secret.
            "secrets_excluded": ["credentials"],
            "original_import_files": len(import_sources),
            "original_import_files_present": len(present_imports),
            "missing_original_import_files": len(import_manifest) - len(present_imports),
            "asset_files": sum(1 for item in asset_manifest if not item.get("missing")),
            "missing_asset_files": sum(1 for item in asset_manifest if item.get("missing")),
        }
        _put_json(archive, "manifest.json", manifest)
        _put_json(archive, "project.json", _row(project, exclude={"owner_id"}))
        _put_json(archive, "chapters.json", [_row(item) for item in chapters])
        _put_json(archive, "revisions.json", [_row(item) for item in revisions])
        _put_json(archive, "canon.json", [_row(item) for item in canon])
        _put_json(archive, "timeline.json", [_row(item) for item in timeline])
        _put_json(archive, "plot_threads.json", [_row(item) for item in threads])
        _put_json(archive, "review_bundles.json", [_row(item) for item in bundles])
        _put_json(
            archive,
            "import_sources.json",
            [_row(item, exclude={"stored_name"}) for item in import_sources],
        )
        _put_json(archive, "summaries.json", optional_rows["summaries"])
        _put_json(archive, "summary_revisions.json", optional_rows["summary_revisions"])
        _put_json(archive, "memory_build_runs.json", optional_rows["memory_build_runs"])
        _put_json(
            archive,
            "memory_build_artifacts.json",
            optional_rows["memory_build_artifacts"],
        )
        _put_json(archive, "characters.json", optional_rows["characters"])
        _put_json(archive, "character_revisions.json", optional_rows["character_revisions"])
        _put_json(archive, "graph_nodes.json", optional_rows["graph_nodes"])
        _put_json(archive, "graph_edges.json", optional_rows["graph_edges"])
        _put_json(archive, "graph_layout.json", optional_rows["graph_layout"])
        _put_json(archive, "assets.json", optional_rows["assets"])
        _put_json(archive, "assets_manifest.json", asset_manifest)
        _put_json(archive, "original_imports_manifest.json", import_manifest)
        _put_json(
            archive,
            "assistant_conversations.json",
            optional_rows["assistant_conversations"],
        )
        _put_json(archive, "assistant_messages.json", optional_rows["assistant_messages"])
        _put_json(archive, "assistant_runs.json", optional_rows["assistant_runs"])
        _put_json(archive, "assistant_events.json", optional_rows["assistant_events"])
        _put_json(archive, "assistant_tool_calls.json", optional_rows["assistant_tool_calls"])
        _put_json(archive, "assistant_change_sets.json", optional_rows["assistant_change_sets"])
        _put_json(archive, "assistant_proposals.json", optional_rows["assistant_proposals"])
        for archive_name, payload in asset_payloads.items():
            archive.writestr(archive_name, payload)
        for chapter in chapters:
            current = next(
                (
                    item
                    for item in revisions
                    if item.chapter_id == chapter.id and item.id == chapter.current_revision_id
                ),
                None,
            )
            if current is None:
                current = next((item for item in revisions if item.chapter_id == chapter.id), None)
            body = current.content if current is not None else ""
            safe_title = safe_text(chapter.title).replace("/", "_").replace("\\", "_")
            filename = f"chapters/{int(chapter.chapter_number):04d}-{safe_title}.md"
            archive.writestr(filename, f"# {chapter.title}\n\n{body}".encode())
        for item in present_imports:
            source_path = import_paths.get(str(item["source_id"]))
            if source_path is None:
                continue
            if upload_root not in source_path.parents or not source_path.is_file():
                continue
            archive_name = item.get("archive_name")
            if archive_name:
                archive.writestr(str(archive_name), source_path.read_bytes())
    return buffer.getvalue()


def restore_project_zip(
    session: Session,
    raw: bytes,
    *,
    owner_id: str,
    project_id: str | None = None,
) -> Any:
    """Restore a project and remove any sidecars if the transaction fails."""

    journal = _RestoreFileJournal(DATA_DIR.resolve())
    try:
        project = _restore_project_zip(
            session,
            raw,
            owner_id=owner_id,
            project_id=project_id,
            journal=journal,
        )
    except Exception:
        # A failed flush/commit can happen after media or original imports
        # have already been materialised.  Roll back both sides explicitly;
        # closing a request session is not a sufficient filesystem journal.
        try:
            session.rollback()
        finally:
            journal.rollback()
        raise
    return project


def _restore_project_zip(
    session: Session,
    raw: bytes,
    *,
    owner_id: str,
    project_id: str | None,
    journal: _RestoreFileJournal,
) -> Any:
    """Restore a backup into a project, preserving imported revision history.

    Existing projects are not overwritten.  When ``project_id`` is omitted a
    new project is created; this operation is intentionally explicit and does
    not restore provider credentials.
    """

    from ..models import (
        CanonItem,
        Chapter,
        ChapterRevision,
        ImportSource,
        PlotThread,
        Project,
        ReviewBundle,
        TimelineEvent,
    )

    try:
        opened_archive = zipfile.ZipFile(io.BytesIO(raw), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("不是有效的项目备份") from exc
    with opened_archive as archive:
        names = _validate_archive(archive)
        if "manifest.json" not in names or "project.json" not in names:
            raise ValueError("不是有效的项目备份")
        manifest = _read_json_member(archive, "manifest.json", default={})
        if not isinstance(manifest, dict):
            raise ValueError("项目备份中的 manifest.json 格式错误")
        schema_major = str(manifest.get("schema_version", "1.0")).split(".")[0]
        if schema_major not in SUPPORTED_EXPORT_MAJORS:
            raise ValueError("项目备份版本不兼容")
        project_data = _read_json_member(archive, "project.json", default={})
        if not isinstance(project_data, dict):
            raise ValueError("项目备份中的 project.json 格式错误")
        if project_id:
            project = session.scalar(
                select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
            )
            if project is None:
                raise LookupError("目标项目不存在")
        else:
            project = Project(
                owner_id=owner_id,
                name=str(project_data.get("name") or project_data.get("title") or "恢复项目"),
                description=project_data.get("description"),
                story_bible=project_data.get("story_bible"),
                source_hash=project_data.get("source_hash"),
                source_filename=project_data.get("source_filename"),
                source_encoding=project_data.get("source_encoding"),
                genre=project_data.get("genre"),
                viewpoint=project_data.get("viewpoint"),
                style=project_data.get("style"),
                target_word_count=project_data.get("target_word_count"),
                must_happen=project_data.get("must_happen", []),
                must_not_happen=project_data.get("must_not_happen", []),
                hard_constraints=project_data.get("hard_constraints", []),
                outline=project_data.get("outline", {}),
                canon_version=int(project_data.get("canon_version") or 0),
                memory_epoch=int(project_data.get("memory_epoch") or 0),
                needs_rebuild=bool(project_data.get("needs_rebuild", False)),
            )
            session.add(project)
            session.flush()
        original_project_id = str(project_data.get("id") or manifest.get("project_id") or "")
        maps: dict[str, dict[str, Any]] = {
            "project": {original_project_id: project} if original_project_id else {},
            "chapter": {},
            "revision": {},
            "canon": {},
            "timeline_event": {},
            "plot_thread": {},
            "summaries": {},
            "summary_revisions": {},
            "memory_build_runs": {},
            "memory_build_artifacts": {},
            "assets": {},
            "characters": {},
            "character_revisions": {},
            "graph_nodes": {},
            "graph_edges": {},
            "graph_layout": {},
            "assistant_conversations": {},
            "assistant_messages": {},
            "assistant_runs": {},
            "assistant_events": {},
            "assistant_tool_calls": {},
            "assistant_change_sets": {},
            "assistant_proposals": {},
        }
        chapter_map: dict[str, Any] = {}
        chapters = _read_json_member(archive, "chapters.json", default=[])
        if not isinstance(chapters, list):
            raise ValueError("项目备份中的 chapters.json 格式错误")
        for data in chapters:
            old_id = str(data.get("id"))
            existing = session.scalar(
                select(Chapter).where(
                    Chapter.project_id == project.id,
                    Chapter.chapter_number == data.get("chapter_number"),
                )
            )
            chapter = existing or Chapter(
                project_id=project.id,
                volume_number=int(data.get("volume_number") or 1),
                chapter_number=int(data.get("chapter_number") or 1),
                sort_order=int(data.get("sort_order") or data.get("chapter_number") or 1),
                title=str(data.get("title") or "未命名章节"),
                status=str(data.get("status") or "draft"),
                summary=data.get("summary"),
                summary_status=str(data.get("summary_status") or "current"),
                confirmed_at=_datetime_value(data.get("confirmed_at")),
            )
            if existing is None:
                session.add(chapter)
                session.flush()
            chapter_map[old_id] = chapter
            maps["chapter"][old_id] = chapter
        revisions = _read_json_member(archive, "revisions.json", default=[])
        if not isinstance(revisions, list):
            raise ValueError("项目备份中的 revisions.json 格式错误")
        revision_map: dict[str, Any] = {}
        for data in revisions:
            chapter = chapter_map.get(str(data.get("chapter_id")))
            if chapter is None:
                continue
            existing = session.scalar(
                select(ChapterRevision).where(
                    ChapterRevision.chapter_id == chapter.id,
                    ChapterRevision.content_hash == data.get("content_hash"),
                )
            )
            revision = existing or ChapterRevision(
                chapter_id=chapter.id,
                revision_number=int(data.get("revision_number") or 1),
                content=str(data.get("content") or ""),
                content_hash=str(
                    data.get("content_hash") or content_hash(str(data.get("content") or ""))
                ),
                source_type=str(data.get("source_type") or "restore"),
                prompt_version=data.get("prompt_version"),
                model_name=data.get("model_name"),
                parent_revision_id=(
                    revision_map.get(str(data.get("parent_revision_id"))).id
                    if revision_map.get(str(data.get("parent_revision_id")))
                    else None
                ),
                is_generated=bool(data.get("is_generated", False)),
                extra=data.get("extra") or {},
            )
            if existing is None:
                session.add(revision)
                session.flush()
            revision_map[str(data.get("id"))] = revision
            maps["revision"][str(data.get("id"))] = revision
        for data in chapters:
            chapter = chapter_map.get(str(data.get("id")))
            revision = revision_map.get(str(data.get("current_revision_id")))
            if chapter is not None and revision is not None:
                chapter.current_revision_id = revision.id
            accepted_source_id = data.get("accepted_revision_id")
            if not accepted_source_id and str(data.get("status") or "") in {
                "confirmed",
                "accepted",
                "published",
                "committed",
            }:
                accepted_source_id = data.get("current_revision_id")
            accepted = revision_map.get(str(accepted_source_id))
            if chapter is not None:
                chapter.accepted_revision_id = accepted.id if accepted is not None else None
                chapter.summary_status = str(data.get("summary_status") or chapter.summary_status)
        current_chapter = chapter_map.get(str(project_data.get("current_chapter_id")))
        if current_chapter is not None:
            project.current_chapter_id = current_chapter.id

        canon = _read_json_member(archive, "canon.json", default=[])
        if not isinstance(canon, list):
            raise ValueError("项目备份中的 canon.json 格式错误")
        canon_map: dict[str, Any] = {}
        for data in canon:
            existing_item = session.scalar(
                select(CanonItem).where(
                    CanonItem.project_id == project.id,
                    CanonItem.key == data.get("key"),
                    CanonItem.canon_version == data.get("canon_version"),
                )
            )
            if existing_item is not None:
                old_id = str(data.get("id") or "")
                if old_id:
                    canon_map[old_id] = existing_item
                    maps["canon"][old_id] = existing_item
                continue
            values = {
                **data,
                "project_id": project.id,
                "value_text": data.get("value_text") or _json_text(data.get("value")),
                "source_revision_id": revision_map.get(str(data.get("source_revision_id")), None).id
                if revision_map.get(str(data.get("source_revision_id")))
                else None,
                "source_chapter_id": chapter_map.get(str(data.get("source_chapter_id")), None).id
                if chapter_map.get(str(data.get("source_chapter_id")))
                else None,
                "superseded_by_id": None,
            }
            values.pop("id", None)
            values.pop("created_at", None)
            values.pop("updated_at", None)
            item = CanonItem(**mapped_kwargs(CanonItem, values))
            session.add(item)
            session.flush()
            canon_map[str(data.get("id"))] = item
            maps["canon"][str(data.get("id"))] = item
        for data in canon:
            item = canon_map.get(str(data.get("id")))
            replacement = canon_map.get(str(data.get("superseded_by_id")))
            if item is not None:
                item.superseded_by_id = replacement.id if replacement is not None else None
        timeline = _read_json_member(archive, "timeline.json", default=[])
        if not isinstance(timeline, list):
            raise ValueError("项目备份中的 timeline.json 格式错误")
        for data in timeline:
            values = {**data, "project_id": project.id}
            values.pop("id", None)
            values.pop("created_at", None)
            values["source_revision_id"] = (
                revision_map.get(str(data.get("source_revision_id"))).id
                if revision_map.get(str(data.get("source_revision_id")))
                else None
            )
            values["chapter_id"] = (
                chapter_map.get(str(data.get("chapter_id"))).id
                if chapter_map.get(str(data.get("chapter_id")))
                else None
            )
            item = TimelineEvent(**mapped_kwargs(TimelineEvent, values))
            session.add(item)
            session.flush()
            maps["timeline_event"][str(data.get("id") or "")] = item
        threads = _read_json_member(archive, "plot_threads.json", default=[])
        if not isinstance(threads, list):
            raise ValueError("项目备份中的 plot_threads.json 格式错误")
        for data in threads:
            values = {**data, "project_id": project.id}
            values.pop("id", None)
            values.pop("created_at", None)
            values.pop("updated_at", None)
            item = PlotThread(**mapped_kwargs(PlotThread, values))
            session.add(item)
            session.flush()
            maps["plot_thread"][str(data.get("id") or "")] = item
        review_rows = _read_json_member(archive, "review_bundles.json", default=[])
        if not isinstance(review_rows, list):
            raise ValueError("项目备份中的 review_bundles.json 格式错误")
        for data in review_rows:
            old_chapter_id = str(data.get("chapter_id") or "")
            old_revision_id = str(data.get("draft_revision_id") or "")
            restored_chapter = chapter_map.get(old_chapter_id)
            restored_revision = revision_map.get(old_revision_id)
            changes = data.get("canon_changes") if isinstance(data.get("canon_changes"), list) else []
            remapped_changes: list[Any] = []
            for change in changes:
                if not isinstance(change, dict):
                    remapped_changes.append(change)
                    continue
                remapped = dict(change)
                source_revision = revision_map.get(str(change.get("source_revision_id") or ""))
                source_chapter = chapter_map.get(str(change.get("source_chapter_id") or ""))
                if source_revision is not None:
                    remapped["source_revision_id"] = source_revision.id
                if source_chapter is not None:
                    remapped["source_chapter_id"] = source_chapter.id
                remapped_changes.append(remapped)
            raw_sources = (
                data.get("source_context")
                if isinstance(data.get("source_context"), list)
                else []
            )
            remapped_sources: list[Any] = []
            for source in raw_sources:
                if not isinstance(source, dict):
                    remapped_sources.append(source)
                    continue
                remapped_source = dict(source)
                source_revision = revision_map.get(str(source.get("revision_id") or ""))
                source_chapter = chapter_map.get(str(source.get("chapter_id") or ""))
                if source_revision is not None:
                    remapped_source["revision_id"] = source_revision.id
                if source_chapter is not None:
                    remapped_source["chapter_id"] = source_chapter.id
                remapped_sources.append(remapped_source)
            if restored_revision is not None and session.scalar(
                select(ReviewBundle).where(
                    ReviewBundle.project_id == project.id,
                    ReviewBundle.draft_revision_id == restored_revision.id,
                    ReviewBundle.status == str(data.get("status") or "stale"),
                )
            ):
                continue
            session.add(
                ReviewBundle(
                    project_id=project.id,
                    chapter_id=restored_chapter.id if restored_chapter is not None else None,
                    generation_run_id=None,
                    base_canon_version=int(data.get("base_canon_version") or 0),
                    base_memory_epoch=int(data.get("base_memory_epoch") or 0),
                    status=str(data.get("status") or "stale"),
                    draft_revision_id=(
                        restored_revision.id if restored_revision is not None else None
                    ),
                    canon_changes=remapped_changes,
                    summary_candidate=str(data.get("summary_candidate") or ""),
                    structured_candidates=(
                        data.get("structured_candidates")
                        if isinstance(data.get("structured_candidates"), dict)
                        else {}
                    ),
                    audit_issues=(
                        data.get("audit_issues")
                        if isinstance(data.get("audit_issues"), list)
                        else []
                    ),
                    source_context=remapped_sources,
                    rejection_reason=data.get("rejection_reason"),
                    force_accept_reason=data.get("force_accept_reason"),
                    resolved_at=_datetime_value(data.get("resolved_at")),
                )
            )
        import_rows = _read_json_member(archive, "import_sources.json", default=[])
        if not isinstance(import_rows, list):
            raise ValueError("项目备份中的 import_sources.json 格式错误")
        upload_root = (DATA_DIR / "uploads").resolve()
        project_upload_root = (upload_root / owner_id / str(project.id)).resolve()
        if upload_root not in project_upload_root.parents:
            raise ValueError("原始导入文件路径无效")
        project_upload_root.mkdir(parents=True, exist_ok=True)
        for data in import_rows:
            old_id = str(data.get("id") or "")
            archive_name = next(
                (name for name in names if name.startswith(f"original_imports/{old_id}-")),
                None,
            )
            if archive_name is None:
                continue
            raw_source = archive.read(archive_name)
            actual_hash = content_hash(raw_source)
            declared_hash = str(data.get("source_hash") or actual_hash)
            if declared_hash != actual_hash:
                raise ValueError("原始导入文件哈希校验失败")
            stored_name = f"{owner_id}/{project.id}/{actual_hash}.source"
            destination = (upload_root / stored_name).resolve()
            if destination.parent != project_upload_root:
                raise ValueError("原始导入文件路径无效")
            if destination.exists():
                if not destination.is_file() or destination.read_bytes() != raw_source:
                    raise ValueError("原始导入文件目标冲突")
            else:
                _write_restore_file(destination, raw_source, journal)
            existing_source = session.scalar(
                select(ImportSource).where(
                    ImportSource.project_id == project.id,
                    ImportSource.source_hash == actual_hash,
                )
            )
            if existing_source is None:
                session.add(
                    ImportSource(
                        project_id=project.id,
                        filename=PurePath(str(data.get("filename") or "未命名稿件.txt")).name,
                        source_hash=actual_hash,
                        encoding=data.get("encoding"),
                        stored_name=stored_name,
                        byte_size=len(raw_source),
                    )
                )
        _restore_optional_data(
            session,
            archive,
            names,
            owner_id=owner_id,
            project_id=str(project.id),
            maps=maps,
            journal=journal,
        )
        session.commit()
        return project


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
