"""API routers for the core story workspace."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Chapter, ChapterRevision, Project
from ..schemas import ChapterRead, ChapterRevisionRead


def require_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def require_chapter(db: Session, chapter_id: str) -> Chapter:
    chapter = db.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


def chapter_payload(chapter: Chapter) -> ChapterRead:
    current: ChapterRevision | None = None
    if chapter.current_revision_id:
        current = next(
            (
                revision
                for revision in chapter.revisions
                if revision.id == chapter.current_revision_id
            ),
            None,
        )
    data = ChapterRead.model_validate(chapter)
    if current is not None:
        data.current_revision = ChapterRevisionRead.model_validate(current)
    return data


__all__ = ["chapter_payload", "require_chapter", "require_project"]
