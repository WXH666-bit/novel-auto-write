"""Chapter, immutable revision, and invalidation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db, rebuild_search_index
from ..models import (
    AuditLog,
    CanonItem,
    Chapter,
    ChapterRevision,
    GenerationRun,
    Project,
    ReviewBundle,
    TimelineEvent,
    utcnow,
)
from ..schemas import (
    ChapterCreate,
    ChapterRead,
    ChapterRevisionCreate,
    ChapterRevisionRead,
    ChapterUpdate,
)
from . import chapter_payload, require_chapter, require_project

router = APIRouter(prefix="/api", tags=["chapters"])


def _new_revision(chapter: Chapter, payload: ChapterRevisionCreate) -> ChapterRevision:
    previous = chapter.revisions[-1] if chapter.revisions else None
    parent_id = payload.parent_revision_id or (previous.id if previous else None)
    return ChapterRevision(
        chapter_id=chapter.id,
        revision_number=(previous.revision_number + 1 if previous else 1),
        content=payload.content,
        content_hash=ChapterRevision.hash_content(payload.content),
        source_type=payload.source_type,
        prompt_version=payload.prompt_version,
        model_name=payload.model_name,
        parent_revision_id=parent_id,
        is_generated=payload.is_generated,
        extra=payload.extra,
    )


def _is_confirmed(chapter: Chapter) -> bool:
    return (
        chapter.status in {"confirmed", "accepted", "published", "committed"}
        or chapter.confirmed_at is not None
    )


def _invalidate_after_edit(db: Session, project: Project, chapter: Chapter) -> dict[str, int]:
    """Mark all downstream derived memory as stale after a confirmed edit."""

    impacted_chapters = db.scalars(
        select(Chapter).where(
            Chapter.project_id == project.id,
            or_(
                Chapter.sort_order > chapter.sort_order,
                and_(
                    Chapter.sort_order == chapter.sort_order,
                    Chapter.chapter_number > chapter.chapter_number,
                ),
            ),
        )
    ).all()
    impacted_ids = {item.id for item in impacted_chapters}
    impacted_ids.add(chapter.id)
    old_revision_ids = {
        revision.id for revision in chapter.revisions if revision.id != chapter.current_revision_id
    }
    for later in impacted_chapters:
        later.summary_status = "needs_review"

    affected_canon = db.scalars(
        select(CanonItem).where(
            CanonItem.project_id == project.id,
            (CanonItem.source_chapter_id.in_(impacted_ids))
            | (CanonItem.source_revision_id.in_(old_revision_ids) if old_revision_ids else False),
        )
    ).all()
    for item in affected_canon:
        item.status = "needs_review"
    timeline = db.scalars(
        select(TimelineEvent).where(
            TimelineEvent.project_id == project.id,
            TimelineEvent.chapter_id.in_(impacted_ids),
        )
    ).all()
    for event in timeline:
        event.needs_review = True
    stale_bundles = db.scalars(
        select(ReviewBundle).where(
            ReviewBundle.project_id == project.id,
            ReviewBundle.status.in_(("pending", "needs_review")),
        )
    ).all()
    for bundle in stale_bundles:
        bundle.status = "stale"
        if bundle.generation_run_id:
            run = db.get(GenerationRun, bundle.generation_run_id)
            if run is not None:
                run.status = "cancelled"
                run.error = "旧章修改使生成上下文失效"
    project.needs_rebuild = True
    project.memory_epoch = int(project.memory_epoch or 0) + 1
    chapter.status = "needs_review"
    chapter.confirmed_at = None
    return {"chapters": len(impacted_chapters), "canon_items": len(affected_canon)}


@router.get("/projects/{project_id}/chapters", response_model=list[ChapterRead])
def list_chapters(project_id: str, db: Session = Depends(get_db)) -> list[ChapterRead]:
    require_project(db, project_id)
    chapters = db.scalars(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order, Chapter.chapter_number)
    ).all()
    return [chapter_payload(chapter) for chapter in chapters]


@router.post(
    "/projects/{project_id}/chapters",
    response_model=ChapterRead,
    status_code=status.HTTP_201_CREATED,
)
def create_chapter(
    project_id: str, payload: ChapterCreate, db: Session = Depends(get_db)
) -> ChapterRead:
    project = require_project(db, project_id)
    chapters = db.scalars(select(Chapter).where(Chapter.project_id == project_id)).all()
    next_number = max((chapter.chapter_number for chapter in chapters), default=0) + 1
    next_order = max((chapter.sort_order for chapter in chapters), default=-1) + 1
    chapter = Chapter(
        project_id=project_id,
        volume_number=payload.volume_number,
        chapter_number=payload.chapter_number or next_number,
        sort_order=payload.sort_order if payload.sort_order is not None else next_order,
        title=payload.title,
        status=payload.status,
        summary=payload.summary,
    )
    db.add(chapter)
    try:
        db.flush()
        if payload.content is not None:
            revision = _new_revision(
                chapter,
                ChapterRevisionCreate(content=payload.content, source_type="manual"),
            )
            db.add(revision)
            db.flush()
            chapter.current_revision_id = revision.id
        project.current_chapter_id = chapter.id
        db.add(
            AuditLog(
                project_id=project_id,
                action="chapter.created",
                entity_type="chapter",
                entity_id=chapter.id,
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="章节编号已存在") from exc
    db.refresh(chapter)
    return chapter_payload(chapter)


@router.get("/chapters/{chapter_id}", response_model=ChapterRead)
def get_chapter(chapter_id: str, db: Session = Depends(get_db)) -> ChapterRead:
    return chapter_payload(require_chapter(db, chapter_id))


@router.patch("/chapters/{chapter_id}", response_model=ChapterRead)
def update_chapter(
    chapter_id: str, payload: ChapterUpdate, db: Session = Depends(get_db)
) -> ChapterRead:
    chapter = require_chapter(db, chapter_id)
    project = require_project(db, chapter.project_id)
    values = payload.model_dump(exclude_unset=True)
    content = values.pop("content", None)
    source_type = values.pop("source_type", None) or "manual"
    is_generated = bool(values.pop("is_generated", False))
    was_confirmed = _is_confirmed(chapter)
    before = {key: getattr(chapter, key) for key in values}
    for key, value in values.items():
        setattr(chapter, key, value)
    if content is not None:
        revision = _new_revision(
            chapter,
            ChapterRevisionCreate(
                content=content,
                source_type=source_type,
                is_generated=is_generated,
            ),
        )
        db.add(revision)
        db.flush()
        chapter.current_revision_id = revision.id
        if was_confirmed:
            _invalidate_after_edit(db, project, chapter)
    chapter.updated_at = utcnow()
    db.add(
        AuditLog(
            project_id=chapter.project_id,
            action="chapter.updated",
            entity_type="chapter",
            entity_id=chapter.id,
            before_json=before,
            after_json={key: getattr(chapter, key) for key in values},
        )
    )
    db.commit()
    db.refresh(chapter)
    if content is not None:
        try:
            rebuild_search_index(db_engine=db.get_bind())
        except Exception:
            pass
    return chapter_payload(chapter)


@router.post(
    "/chapters/{chapter_id}/revisions",
    response_model=ChapterRevisionRead,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/chapters/{chapter_id}/revision",
    response_model=ChapterRevisionRead,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_revision(
    chapter_id: str, payload: ChapterRevisionCreate, db: Session = Depends(get_db)
) -> ChapterRevisionRead:
    chapter = require_chapter(db, chapter_id)
    project = require_project(db, chapter.project_id)
    was_confirmed = _is_confirmed(chapter)
    revision = _new_revision(chapter, payload)
    db.add(revision)
    try:
        db.flush()
        chapter.current_revision_id = revision.id
        chapter.updated_at = utcnow()
        invalidation = {"chapters": 0, "canon_items": 0}
        if was_confirmed:
            invalidation = _invalidate_after_edit(db, project, chapter)
        db.add(
            AuditLog(
                project_id=project.id,
                action="chapter.revision.created",
                entity_type="chapter_revision",
                entity_id=revision.id,
                after_json={
                    "revision_number": revision.revision_number,
                    "content_hash": revision.content_hash,
                    "invalidation": invalidation,
                },
                reason=(
                    "confirmed chapter edited; downstream memory invalidated"
                    if was_confirmed
                    else None
                ),
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="修订编号冲突，请重试") from exc
    # FTS is derived state; rebuild after the revision transaction succeeds.
    try:
        rebuild_search_index(db_engine=db.get_bind())
    except Exception:
        # A later startup can rebuild the index.  Never undo an accepted
        # revision because a derived search index was temporarily unavailable.
        pass
    db.refresh(revision)
    return ChapterRevisionRead.model_validate(revision)


@router.get("/chapters/{chapter_id}/revisions", response_model=list[ChapterRevisionRead])
def list_revisions(chapter_id: str, db: Session = Depends(get_db)) -> list[ChapterRevisionRead]:
    chapter = require_chapter(db, chapter_id)
    return [ChapterRevisionRead.model_validate(revision) for revision in chapter.revisions]


@router.get("/chapters/{chapter_id}/revisions/{revision_id}", response_model=ChapterRevisionRead)
def get_revision(
    chapter_id: str, revision_id: str, db: Session = Depends(get_db)
) -> ChapterRevisionRead:
    require_chapter(db, chapter_id)
    revision = db.scalar(
        select(ChapterRevision).where(
            ChapterRevision.id == revision_id,
            ChapterRevision.chapter_id == chapter_id,
        )
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="修订不存在")
    return ChapterRevisionRead.model_validate(revision)


@router.post("/chapters/{chapter_id}/confirm", response_model=ChapterRead)
def confirm_chapter(chapter_id: str, db: Session = Depends(get_db)) -> ChapterRead:
    chapter = require_chapter(db, chapter_id)
    project = require_project(db, chapter.project_id)
    if not chapter.current_revision_id:
        raise HTTPException(status_code=422, detail="章节还没有可确认的修订")
    chapter.status = "confirmed"
    chapter.confirmed_at = utcnow()
    chapter.summary_status = "current"
    chapter.accepted_revision_id = chapter.current_revision_id
    project.memory_epoch = int(project.memory_epoch or 0) + 1
    db.add(
        AuditLog(
            project_id=chapter.project_id,
            action="chapter.confirmed",
            entity_type="chapter",
            entity_id=chapter.id,
        )
    )
    db.commit()
    db.refresh(chapter)
    return chapter_payload(chapter)


__all__ = ["router"]
