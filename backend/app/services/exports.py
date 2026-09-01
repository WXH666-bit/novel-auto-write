"""Schema-versioned project ZIP backup and restore helpers."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime
from pathlib import PurePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DATA_DIR
from .common import mapped_kwargs, safe_text
from .importer import content_hash

EXPORT_SCHEMA_VERSION = "2.0"
SUPPORTED_EXPORT_MAJORS = {"1", EXPORT_SCHEMA_VERSION.split(".")[0]}
MAX_ARCHIVE_ENTRIES = 20_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_JSON_MEMBER_BYTES = 64 * 1024 * 1024


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
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
        name: _jsonable(getattr(item, name, None))
        for name in names
        if name not in exclude and not name.startswith("_")
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
        normalized = PurePath(info.filename)
        if normalized.is_absolute() or ".." in normalized.parts:
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
                "chapters/",
                "original_imports/",
            ],
            # Provider profiles (and therefore their credentials) are never
            # exported.  Avoid writing a credential field name into the archive
            # as well, so a simple backup scan cannot mistake metadata for a
            # leaked secret.
            "secrets_excluded": ["credentials"],
            "original_import_files": len(import_sources),
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
        upload_root = (DATA_DIR / "uploads").resolve()
        for source in import_sources:
            source_path = (upload_root / source.stored_name).resolve()
            if upload_root not in source_path.parents or not source_path.is_file():
                continue
            safe_name = PurePath(source.filename).name.replace("/", "_").replace("\\", "_")
            archive.writestr(
                f"original_imports/{source.id}-{safe_name}",
                source_path.read_bytes(),
            )
    return buffer.getvalue()


def restore_project_zip(
    session: Session,
    raw: bytes,
    *,
    owner_id: str,
    project_id: str | None = None,
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
            if session.scalar(
                select(CanonItem).where(
                    CanonItem.project_id == project.id,
                    CanonItem.key == data.get("key"),
                    CanonItem.canon_version == data.get("canon_version"),
                )
            ):
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
            session.add(TimelineEvent(**mapped_kwargs(TimelineEvent, values)))
        threads = _read_json_member(archive, "plot_threads.json", default=[])
        if not isinstance(threads, list):
            raise ValueError("项目备份中的 plot_threads.json 格式错误")
        for data in threads:
            values = {**data, "project_id": project.id}
            values.pop("id", None)
            values.pop("created_at", None)
            values.pop("updated_at", None)
            session.add(PlotThread(**mapped_kwargs(PlotThread, values)))
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
            if not destination.exists():
                destination.write_bytes(raw_source)
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
        session.commit()
        return project


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
