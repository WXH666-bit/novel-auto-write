"""Canon query and review-gate endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, rebuild_search_index
from ..models import (
    AuditLog,
    CanonItem,
    Chapter,
    ChapterRevision,
    Project,
    User,
    json_text,
    utcnow,
)
from ..schemas import CanonConfirmRequest, CanonItemCreate, CanonItemRead, CanonItemUpdate
from ..security import get_current_user
from . import require_canon_item, require_project

router = APIRouter(prefix="/api", tags=["canon"])
_STATUS_ALIASES = {
    "待确认": "pending",
    "已确认": "confirmed",
    "已取代": "superseded",
    "待复核": "needs_review",
}


def _validate_sources(
    db: Session, project_id: str, chapter_id: str | None, revision_id: str | None
) -> None:
    revision: ChapterRevision | None = None
    if chapter_id is not None:
        chapter = db.scalar(
            select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
        )
        if chapter is None:
            raise HTTPException(status_code=422, detail="来源章节不属于该项目")
    if revision_id is not None:
        revision = db.scalar(
            select(ChapterRevision)
            .join(Chapter, Chapter.id == ChapterRevision.chapter_id)
            .where(ChapterRevision.id == revision_id, Chapter.project_id == project_id)
        )
        if revision is None:
            raise HTTPException(status_code=422, detail="来源修订不属于该项目")
    if chapter_id is not None and revision is not None and revision.chapter_id != chapter_id:
        raise HTTPException(status_code=422, detail="来源修订不属于所选来源章节")


@router.get("/projects/{project_id}/canon", response_model=list[CanonItemRead])
@router.get(
    "/projects/{project_id}/canon-items",
    response_model=list[CanonItemRead],
    include_in_schema=False,
)
def list_canon(
    project_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CanonItemRead]:
    require_project(db, project_id, current_user)
    statement = (
        select(CanonItem).where(CanonItem.project_id == project_id).order_by(CanonItem.created_at)
    )
    if status_filter:
        statement = statement.where(CanonItem.status == status_filter)
    if category:
        statement = statement.where(CanonItem.category == category)
    return [CanonItemRead.model_validate(item) for item in db.scalars(statement).all()]


@router.post(
    "/projects/{project_id}/canon",
    response_model=CanonItemRead,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/projects/{project_id}/canon-items",
    response_model=CanonItemRead,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_canon(
    project_id: str,
    payload: CanonItemCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CanonItemRead:
    project = require_project(db, project_id, current_user)
    _validate_sources(db, project_id, payload.source_chapter_id, payload.source_revision_id)
    normalized_status = _STATUS_ALIASES.get(payload.status, payload.status)
    if normalized_status not in {"pending", "needs_review", "superseded"}:
        raise HTTPException(status_code=422, detail="新建正典必须先进入待确认状态")
    item = CanonItem(
        project_id=project_id,
        category=payload.category,
        key=payload.key,
        value=payload.value,
        value_text=json_text(payload.value),
        aliases=payload.aliases,
        status=normalized_status,
        is_hard=payload.is_hard,
        source_revision_id=payload.source_revision_id,
        source_chapter_id=payload.source_chapter_id,
        source_start=payload.source_start,
        source_end=payload.source_end,
        source_excerpt=payload.source_excerpt,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        confidence=payload.confidence,
        note=payload.note,
        canon_version=project.canon_version,
    )
    db.add(item)
    db.flush()
    db.add(
        AuditLog(
            project_id=project_id,
            action="canon.created",
            entity_type="canon_item",
            entity_id=item.id,
            after_json={"key": item.key, "status": item.status},
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
        pass
    db.refresh(item)
    return CanonItemRead.model_validate(item)


@router.get("/canon/{item_id}", response_model=CanonItemRead)
@router.get("/canon-items/{item_id}", response_model=CanonItemRead, include_in_schema=False)
def get_canon(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CanonItemRead:
    return CanonItemRead.model_validate(require_canon_item(db, item_id, current_user))


@router.patch("/canon/{item_id}", response_model=CanonItemRead)
@router.patch("/canon-items/{item_id}", response_model=CanonItemRead, include_in_schema=False)
def update_canon(
    item_id: str,
    payload: CanonItemUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CanonItemRead:
    item = require_canon_item(db, item_id, current_user)
    values = payload.model_dump(exclude_unset=True)
    requested_status = values.get("status")
    if requested_status in _STATUS_ALIASES:
        requested_status = _STATUS_ALIASES[requested_status]
        values["status"] = requested_status
    # Version advancement is kept in the same gate as the explicit confirm
    # endpoint; a PATCH may request confirmation but cannot bypass it.
    if requested_status == "confirmed":
        values.pop("status")
    _validate_sources(
        db,
        item.project_id,
        values.get("source_chapter_id", item.source_chapter_id),
        values.get("source_revision_id", item.source_revision_id),
    )
    before = {key: getattr(item, key) for key in values}
    if "value" in values:
        item.value_text = json_text(values["value"])
    for key, value in values.items():
        setattr(item, key, value)
    if requested_status == "confirmed":
        _confirm_item(db, item, CanonConfirmRequest(), current_user)
    item.updated_at = utcnow()
    db.add(
        AuditLog(
            project_id=item.project_id,
            action="canon.updated",
            entity_type="canon_item",
            entity_id=item.id,
            before_json=before,
            after_json={key: getattr(item, key) for key in values},
            actor_user_id=current_user.id,
        )
    )
    db.commit()
    try:
        rebuild_search_index(
            db_engine=db.get_bind(),
            owner_id=current_user.id,
            project_id=item.project_id,
        )
    except Exception:
        pass
    db.refresh(item)
    return CanonItemRead.model_validate(item)


def _confirm_item(
    db: Session, item: CanonItem, payload: CanonConfirmRequest, current_user: User
) -> CanonItem:
    project = require_project(db, item.project_id, current_user)
    # Serialize every canon pointer advance at the project row.  Refresh the
    # item after acquiring the lock so simultaneous confirmations of the same
    # fact remain idempotent rather than consuming two canon versions.
    db.flush()
    project_id = project.id
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None:  # pragma: no cover - guarded tenant lookup
        raise HTTPException(status_code=404, detail="项目不存在")
    item = db.scalar(
        select(CanonItem)
        .where(CanonItem.id == item.id, CanonItem.project_id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:  # pragma: no cover - guarded tenant lookup
        raise HTTPException(status_code=404, detail="正典条目不存在")
    if item.status == "confirmed":
        return item
    if payload.force and not payload.reason:
        raise HTTPException(status_code=422, detail="强制接受必须填写理由")
    # Confirming a normal item is the only operation that advances the
    # project's canon pointer.  Replaying the request is therefore idempotent.
    project.canon_version += 1
    item.canon_version = project.canon_version
    item.status = "confirmed"
    item.updated_at = utcnow()
    db.add(
        AuditLog(
            project_id=project.id,
            action="canon.force_confirmed" if payload.force else "canon.confirmed",
            entity_type="canon_item",
            entity_id=item.id,
            reason=payload.reason,
            after_json={"canon_version": project.canon_version, "is_hard": item.is_hard},
            actor_user_id=current_user.id,
        )
    )
    return item


@router.post("/canon/{item_id}/confirm", response_model=CanonItemRead)
@router.post(
    "/canon-items/{item_id}/confirm", response_model=CanonItemRead, include_in_schema=False
)
def confirm_canon(
    item_id: str,
    payload: CanonConfirmRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CanonItemRead:
    item = require_canon_item(db, item_id, current_user)
    _confirm_item(db, item, payload or CanonConfirmRequest(), current_user)
    db.commit()
    try:
        rebuild_search_index(
            db_engine=db.get_bind(),
            owner_id=current_user.id,
            project_id=item.project_id,
        )
    except Exception:
        pass
    db.refresh(item)
    return CanonItemRead.model_validate(item)


@router.post(
    "/projects/{project_id}/canon/{item_id}/confirm",
    response_model=CanonItemRead,
    include_in_schema=False,
)
def confirm_project_canon(
    project_id: str,
    item_id: str,
    payload: CanonConfirmRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CanonItemRead:
    require_project(db, project_id, current_user)
    item = require_canon_item(db, item_id, current_user)
    if item.project_id != project_id:
        raise HTTPException(status_code=404, detail="正典条目不存在")
    _confirm_item(db, item, payload or CanonConfirmRequest(), current_user)
    db.commit()
    try:
        rebuild_search_index(
            db_engine=db.get_bind(),
            owner_id=current_user.id,
            project_id=project_id,
        )
    except Exception:
        pass
    db.refresh(item)
    return CanonItemRead.model_validate(item)


@router.post("/canon/{item_id}/needs-review", response_model=CanonItemRead)
@router.post(
    "/canon-items/{item_id}/needs-review", response_model=CanonItemRead, include_in_schema=False
)
def mark_canon_needs_review(
    item_id: str,
    reason: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CanonItemRead:
    item = require_canon_item(db, item_id, current_user)
    item.status = "needs_review"
    item.updated_at = utcnow()
    db.add(
        AuditLog(
            project_id=item.project_id,
            action="canon.needs_review",
            entity_type="canon_item",
            entity_id=item.id,
            reason=reason,
            actor_user_id=current_user.id,
        )
    )
    db.commit()
    try:
        rebuild_search_index(
            db_engine=db.get_bind(),
            owner_id=current_user.id,
            project_id=item.project_id,
        )
    except Exception:
        pass
    db.refresh(item)
    return CanonItemRead.model_validate(item)


__all__ = ["router"]
