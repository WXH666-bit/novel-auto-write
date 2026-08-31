"""TXT/Markdown import preview and commit endpoints."""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DATA_DIR
from ..db import get_db, rebuild_search_index
from ..models import AuditLog, ImportSource, Project
from ..services.importer import ImportPreview, apply_preview_edits, persist_import, preview_import
from . import chapter_payload

router = APIRouter(prefix="/api/projects", tags=["imports"])
MAX_IMPORT_BYTES = 50 * 1024 * 1024
UPLOAD_DIR = DATA_DIR / "uploads"


def _project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


class ImportChapterPayload(BaseModel):
    ordinal: int = Field(default=1, ge=1)
    title: str = "未命名章节"
    content: str = ""
    source_start: int = Field(default=0, ge=0)
    source_end: int | None = Field(default=None, ge=0)
    source_type: str = "import"
    merge_with_previous: bool = False


class ImportCommitPayload(BaseModel):
    filename: str = "未命名稿件.txt"
    source_hash: str | None = None
    encoding: str | None = None
    content: str | None = None
    chapters: list[ImportChapterPayload] | None = None


def _preview_response(preview: ImportPreview) -> dict[str, Any]:
    return preview.as_dict()


def _cache_source(raw: bytes, source_hash: str) -> str:
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="首版单个导入文件不能超过 50 MB")
    if len(source_hash) != 64 or any(
        char not in "0123456789abcdef" for char in source_hash.lower()
    ):
        raise HTTPException(status_code=422, detail="导入文件哈希无效")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{source_hash.lower()}.source"
    destination = (UPLOAD_DIR / stored_name).resolve()
    if destination.parent != UPLOAD_DIR.resolve():
        raise HTTPException(status_code=422, detail="导入缓存路径无效")
    if not destination.exists():
        destination.write_bytes(raw)
    return stored_name


@router.post("/{project_id}/import/preview")
@router.post("/{project_id}/imports/preview")
def import_preview(
    project_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
) -> dict[str, Any]:
    _project(db, project_id)
    raw = file.file.read()
    preview = preview_import(raw, file.filename or "未命名稿件")
    _cache_source(raw, preview.source_hash)
    return _preview_response(preview)


@router.post("/{project_id}/imports/preview-text")
def import_preview_text(
    project_id: str, payload: ImportCommitPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    _project(db, project_id)
    if payload.content is None:
        raise HTTPException(status_code=422, detail="content 不能为空")
    raw = payload.content.encode("utf-8")
    preview = preview_import(raw, payload.filename)
    _cache_source(raw, preview.source_hash)
    return _preview_response(preview)


@router.post("/{project_id}/import/commit")
@router.post("/{project_id}/imports/commit")
def import_commit(
    project_id: str, payload: ImportCommitPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    project = _project(db, project_id)
    chapters = payload.chapters
    source_hash = payload.source_hash
    if chapters is None:
        if payload.content is None:
            raise HTTPException(status_code=422, detail="请提供 chapters 或 content")
        preview = preview_import(payload.content.encode("utf-8"), payload.filename)
        chapters = [
            ImportChapterPayload(
                ordinal=chapter.ordinal,
                title=chapter.title,
                content=chapter.content,
                source_start=chapter.source_start,
                source_end=chapter.source_end,
                source_type=chapter.source_type,
            )
            for chapter in preview.chapters
        ]
        source_hash = source_hash or preview.source_hash
    # ``apply_preview_edits`` handles merge and makes ordinal ordering explicit.
    preview = ImportPreview(payload.filename, "provided", source_hash or "", [])
    edited = apply_preview_edits(preview, [chapter.model_dump() for chapter in chapters])
    if not edited:
        raise HTTPException(status_code=422, detail="没有可导入的正文")
    created = persist_import(db, project, edited, source_hash=source_hash)
    if not created:
        return {"created": [], "count": 0, "idempotent": True, "source_hash": source_hash}
    if source_hash:
        stored_name = f"{source_hash.lower()}.source"
        stored_path = (UPLOAD_DIR / stored_name).resolve()
        if stored_path.parent == UPLOAD_DIR.resolve() and stored_path.is_file():
            existing_source = db.scalar(
                select(ImportSource).where(
                    ImportSource.project_id == project.id,
                    ImportSource.source_hash == source_hash,
                )
            )
            if existing_source is None:
                db.add(
                    ImportSource(
                        project_id=project.id,
                        filename=PurePath(payload.filename).name[:255] or "未命名稿件.txt",
                        source_hash=source_hash,
                        encoding=payload.encoding,
                        stored_name=stored_name,
                        byte_size=stored_path.stat().st_size,
                    )
                )
        project.source_filename = PurePath(payload.filename).name[:255]
        project.source_encoding = payload.encoding
    db.add(
        AuditLog(
            project_id=project.id,
            action="import.committed",
            entity_type="project",
            entity_id=project.id,
            after_json={"source_hash": source_hash, "chapter_count": len(created)},
        )
    )
    db.commit()
    try:
        rebuild_search_index(db_engine=db.get_bind())
    except Exception:
        pass
    chapter_payloads = [chapter_payload(chapter).model_dump(mode="json") for chapter in created]
    return {
        "created": chapter_payloads,
        "chapters": chapter_payloads,
        "count": len(created),
        "source_hash": source_hash,
    }


# A short alias is useful for clients that do not distinguish preview/commit in
# their navigation.
@router.post("/{project_id}/import")
def import_commit_alias(
    project_id: str, payload: ImportCommitPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return import_commit(project_id, payload, db)
