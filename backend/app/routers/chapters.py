"""Chapter, immutable revision, and invalidation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db, rebuild_search_index
from ..models import (
    AuditLog,
    CanonItem,
    Chapter,
    ChapterRevision,
    GenerationRun,
    Job,
    Project,
    ReviewBundle,
    TimelineEvent,
    User,
    utcnow,
)
from ..schemas import (
    ChapterCreate,
    ChapterRead,
    ChapterRevisionCreate,
    ChapterRevisionRead,
    ChapterUpdate,
)
from ..security import get_current_user
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


def _stale_review_bundles(
    db: Session,
    project: Project,
    *,
    chapter_id: str | None = None,
    error: str,
) -> int:
    """CAS pending bundles to stale without overwriting a concurrent accept."""

    conditions = [
        ReviewBundle.project_id == project.id,
        ReviewBundle.status.in_(("pending", "needs_review")),
    ]
    if chapter_id is not None:
        conditions.append(ReviewBundle.chapter_id == chapter_id)
    candidates = db.execute(
        select(ReviewBundle.id, ReviewBundle.generation_run_id).where(*conditions)
    ).all()
    claimed = 0
    for bundle_id, generation_run_id in candidates:
        result = db.execute(
            update(ReviewBundle)
            .where(
                ReviewBundle.id == bundle_id,
                ReviewBundle.status.in_(("pending", "needs_review")),
            )
            .values(status="stale")
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        claimed += 1
        if not generation_run_id:
            continue
        run = db.get(GenerationRun, generation_run_id)
        if run is None:
            continue
        run.status = "cancelled"
        run.error = error
        if run.idempotency_key:
            job = db.scalar(
                select(Job).where(
                    Job.project_id == project.id,
                    Job.idempotency_key == run.idempotency_key,
                )
            )
            if job is not None:
                job.state = "cancelled"
                job.current_stage = run.stage
                job.last_error = run.error
                job.lease_owner = None
                job.lease_expires_at = None
    return claimed


def _stale_chapter_reviews(db: Session, project: Project, chapter: Chapter) -> int:
    """Invalidate review output tied to prose that a manual edit replaced."""

    return _stale_review_bundles(
        db,
        project,
        chapter_id=chapter.id,
        error="章节手动修改使审核上下文失效",
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
    _stale_review_bundles(
        db,
        project,
        error="旧章修改使生成上下文失效",
    )
    # Increment from the database value so a concurrent accept cannot make
    # this invalidation lose an epoch update through a stale ORM snapshot.
    db.execute(
        update(Project)
        .where(Project.id == project.id)
        .values(
            needs_rebuild=True,
            memory_epoch=Project.memory_epoch + 1,
        )
        .execution_options(synchronize_session=False)
    )
    db.refresh(project, attribute_names=["needs_rebuild", "memory_epoch"])
    chapter.status = "needs_review"
    chapter.confirmed_at = None
    return {"chapters": len(impacted_chapters), "canon_items": len(affected_canon)}


@router.get("/projects/{project_id}/chapters", response_model=list[ChapterRead])
def list_chapters(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ChapterRead]:
    require_project(db, project_id, current_user)
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
    project_id: str,
    payload: ChapterCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChapterRead:
    project = require_project(db, project_id, current_user)
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
                actor_user_id=current_user.id,
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="章节编号已存在") from exc
    db.refresh(chapter)
    return chapter_payload(chapter)


@router.get("/chapters/{chapter_id}", response_model=ChapterRead)
def get_chapter(
    chapter_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChapterRead:
    return chapter_payload(require_chapter(db, chapter_id, current_user))


@router.patch("/chapters/{chapter_id}", response_model=ChapterRead)
def update_chapter(
    chapter_id: str,
    payload: ChapterUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChapterRead:
    chapter = require_chapter(db, chapter_id, current_user)
    project = require_project(db, chapter.project_id, current_user)
    values = payload.model_dump(exclude_unset=True)
    content = values.pop("content", None)
    source_type = values.pop("source_type", None) or "manual"
    is_generated = bool(values.pop("is_generated", False))
    was_confirmed = _is_confirmed(chapter)
    before = {key: getattr(chapter, key) for key in values}
    for key, value in values.items():
        setattr(chapter, key, value)
    if content is not None:
        if was_confirmed:
            _stale_review_bundles(
                db,
                project,
                error="旧章修改使生成上下文失效",
            )
        else:
            _stale_chapter_reviews(db, project, chapter)
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
            actor_user_id=current_user.id,
        )
    )
    db.commit()
    db.refresh(chapter)
    if content is not None or {"title", "summary", "status"}.intersection(values):
        try:
            rebuild_search_index(
                db_engine=db.get_bind(),
                owner_id=current_user.id,
                project_id=project.id,
            )
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
    chapter_id: str,
    payload: ChapterRevisionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChapterRevisionRead:
    chapter = require_chapter(db, chapter_id, current_user)
    project = require_project(db, chapter.project_id, current_user)
    was_confirmed = _is_confirmed(chapter)
    if was_confirmed:
        stale_reviews = _stale_review_bundles(
            db,
            project,
            error="旧章修改使生成上下文失效",
        )
    else:
        stale_reviews = _stale_chapter_reviews(db, project, chapter)
    revision = _new_revision(chapter, payload)
    db.add(revision)
    try:
        db.flush()
        chapter.current_revision_id = revision.id
        chapter.updated_at = utcnow()
        invalidation = {"chapters": 0, "canon_items": 0}
        if was_confirmed:
            invalidation = _invalidate_after_edit(db, project, chapter)
        else:
            invalidation["review_bundles"] = stale_reviews
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
                actor_user_id=current_user.id,
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="修订编号冲突，请重试") from exc
    # FTS is derived state; rebuild after the revision transaction succeeds.
    try:
        rebuild_search_index(
            db_engine=db.get_bind(),
            owner_id=current_user.id,
            project_id=project.id,
        )
    except Exception:
        # A later startup can rebuild the index.  Never undo an accepted
        # revision because a derived search index was temporarily unavailable.
        pass
    db.refresh(revision)
    return ChapterRevisionRead.model_validate(revision)


@router.get("/chapters/{chapter_id}/revisions", response_model=list[ChapterRevisionRead])
def list_revisions(
    chapter_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ChapterRevisionRead]:
    chapter = require_chapter(db, chapter_id, current_user)
    return [ChapterRevisionRead.model_validate(revision) for revision in chapter.revisions]


@router.get("/chapters/{chapter_id}/revisions/{revision_id}", response_model=ChapterRevisionRead)
def get_revision(
    chapter_id: str,
    revision_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChapterRevisionRead:
    require_chapter(db, chapter_id, current_user)
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
def confirm_chapter(
    chapter_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChapterRead:
    chapter = require_chapter(db, chapter_id, current_user)
    project = require_project(db, chapter.project_id, current_user)
    project = db.scalar(
        select(Project)
        .where(Project.id == project.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    chapter = db.scalar(
        select(Chapter)
        .where(Chapter.id == chapter_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None or chapter is None:  # pragma: no cover - guarded tenant lookup
        raise HTTPException(status_code=404, detail="章节不存在")
    if not chapter.current_revision_id:
        raise HTTPException(status_code=422, detail="章节还没有可确认的修订")
    current_revision = db.get(ChapterRevision, chapter.current_revision_id)
    unresolved_bundle = db.scalar(
        select(ReviewBundle).where(
            ReviewBundle.chapter_id == chapter.id,
            ReviewBundle.draft_revision_id == chapter.current_revision_id,
            ReviewBundle.status.notin_(("accepted", "force_accepted")),
        )
    )
    accepted_bundle = db.scalar(
        select(ReviewBundle).where(
            ReviewBundle.chapter_id == chapter.id,
            ReviewBundle.draft_revision_id == chapter.current_revision_id,
            ReviewBundle.status.in_(("accepted", "force_accepted")),
        )
    )
    if unresolved_bundle is not None or (
        current_revision is not None
        and current_revision.is_generated
        and accepted_bundle is None
    ):
        raise HTTPException(
            status_code=409,
            detail="生成草稿必须通过审核包接受，不能直接确认",
        )
    if (
        chapter.status == "confirmed"
        and chapter.accepted_revision_id == chapter.current_revision_id
        and chapter.confirmed_at is not None
    ):
        return chapter_payload(chapter)
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
            actor_user_id=current_user.id,
        )
    )
    db.commit()
    try:
        rebuild_search_index(
            db_engine=db.get_bind(),
            owner_id=current_user.id,
            project_id=project.id,
        )
    except Exception:
        # Search is derived state and is rebuilt again on startup.
        pass
    db.refresh(chapter)
    return chapter_payload(chapter)


__all__ = ["router"]
