"""API routers for the core story workspace."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CanonItem, Chapter, ChapterRevision, GenerationRun, Project, ReviewBundle, User
from ..schemas import ChapterRead, ChapterRevisionRead
from ..security import user_id_of


def require_project(db: Session, project_id: str, user: User | str) -> Project:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == user_id_of(user))
    )
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def require_chapter(db: Session, chapter_id: str, user: User | str) -> Chapter:
    chapter = db.scalar(
        select(Chapter)
        .join(Project, Project.id == Chapter.project_id)
        .where(Chapter.id == chapter_id, Project.owner_id == user_id_of(user))
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


def require_canon_item(db: Session, item_id: str, user: User | str) -> CanonItem:
    item = db.scalar(
        select(CanonItem)
        .join(Project, Project.id == CanonItem.project_id)
        .where(CanonItem.id == item_id, Project.owner_id == user_id_of(user))
    )
    if item is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="正典条目不存在")
    return item


def require_generation(db: Session, run_id: str, user: User | str) -> GenerationRun:
    run = db.scalar(
        select(GenerationRun)
        .join(Project, Project.id == GenerationRun.project_id)
        .where(GenerationRun.id == run_id, Project.owner_id == user_id_of(user))
    )
    if run is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="生成任务不存在")
    return run


def require_review(db: Session, bundle_id: str, user: User | str) -> ReviewBundle:
    bundle = db.scalar(
        select(ReviewBundle)
        .join(Project, Project.id == ReviewBundle.project_id)
        .where(ReviewBundle.id == bundle_id, Project.owner_id == user_id_of(user))
    )
    if bundle is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="审核包不存在")
    return bundle


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


__all__ = [
    "chapter_payload",
    "require_canon_item",
    "require_chapter",
    "require_generation",
    "require_project",
    "require_review",
]
