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

EXPORT_SCHEMA_VERSION = "1.0"


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


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


def export_project_zip(session: Session, project_id: str) -> bytes:
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

    project = session.scalar(select(Project).where(Project.id == project_id))
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
        _put_json(archive, "project.json", _row(project))
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
            filename = f"chapters/{int(chapter.chapter_number):04d}-{safe_text(chapter.title).replace('/', '_')}.md"
            archive.writestr(filename, f"# {chapter.title}\n\n{body}".encode())
        upload_root = (DATA_DIR / "uploads").resolve()
        for source in import_sources:
            source_path = (upload_root / source.stored_name).resolve()
            if source_path.parent != upload_root or not source_path.is_file():
                continue
            safe_name = PurePath(source.filename).name.replace("/", "_").replace("\\", "_")
            archive.writestr(
                f"original_imports/{source.id}-{safe_name}",
                source_path.read_bytes(),
            )
    return buffer.getvalue()


def restore_project_zip(session: Session, raw: bytes, *, project_id: str | None = None) -> Any:
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
        TimelineEvent,
    )

    with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
        names = set(archive.namelist())
        if "manifest.json" not in names or "project.json" not in names:
            raise ValueError("不是有效的项目备份")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            str(manifest.get("schema_version", "")).split(".")[0]
            != EXPORT_SCHEMA_VERSION.split(".")[0]
        ):
            raise ValueError("项目备份版本不兼容")
        project_data = json.loads(archive.read("project.json"))
        if project_id:
            project = session.scalar(select(Project).where(Project.id == project_id))
            if project is None:
                raise LookupError("目标项目不存在")
        else:
            project = Project(
                name=str(project_data.get("name") or project_data.get("title") or "恢复项目"),
                description=project_data.get("description"),
                genre=project_data.get("genre"),
                viewpoint=project_data.get("viewpoint"),
                style=project_data.get("style"),
                target_word_count=project_data.get("target_word_count"),
                must_happen=project_data.get("must_happen", []),
                must_not_happen=project_data.get("must_not_happen", []),
                hard_constraints=project_data.get("hard_constraints", []),
                outline=project_data.get("outline", {}),
                canon_version=int(project_data.get("canon_version") or 0),
            )
            session.add(project)
            session.flush()
        chapter_map: dict[str, Any] = {}
        chapters = json.loads(archive.read("chapters.json")) if "chapters.json" in names else []
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
            )
            if existing is None:
                session.add(chapter)
                session.flush()
            chapter_map[old_id] = chapter
        revisions = json.loads(archive.read("revisions.json")) if "revisions.json" in names else []
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
        current_chapter = chapter_map.get(str(project_data.get("current_chapter_id")))
        if current_chapter is not None:
            project.current_chapter_id = current_chapter.id

        canon = json.loads(archive.read("canon.json")) if "canon.json" in names else []
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
            }
            values.pop("id", None)
            values.pop("created_at", None)
            values.pop("updated_at", None)
            item = CanonItem(**mapped_kwargs(CanonItem, values))
            session.add(item)
        timeline = json.loads(archive.read("timeline.json")) if "timeline.json" in names else []
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
        threads = (
            json.loads(archive.read("plot_threads.json")) if "plot_threads.json" in names else []
        )
        for data in threads:
            values = {**data, "project_id": project.id}
            values.pop("id", None)
            values.pop("created_at", None)
            values.pop("updated_at", None)
            session.add(PlotThread(**mapped_kwargs(PlotThread, values)))
        import_rows = (
            json.loads(archive.read("import_sources.json"))
            if "import_sources.json" in names
            else []
        )
        upload_root = (DATA_DIR / "uploads").resolve()
        upload_root.mkdir(parents=True, exist_ok=True)
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
            stored_name = f"{actual_hash}.source"
            destination = (upload_root / stored_name).resolve()
            if destination.parent != upload_root:
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
