"""Project and story-map endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, rebuild_search_index
from ..models import AuditLog, CanonItem, Chapter, PlotThread, Project, TimelineEvent, User, utcnow
from ..schemas import (
    CanonItemRead,
    PlotThreadRead,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    StoryMapResponse,
    TimelineEventRead,
)
from ..security import get_current_user
from ..services.search import purge_project_search
from ..services.storage import stage_storage_deletion
from . import chapter_payload, require_project

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectRead])
def list_projects(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ProjectRead]:
    projects = db.scalars(
        select(Project)
        .where(Project.owner_id == current_user.id)
        .order_by(Project.updated_at.desc())
    ).all()
    return [ProjectRead.model_validate(project) for project in projects]


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectRead:
    project = Project(
        owner_id=current_user.id,
        name=payload.name or payload.title or "未命名项目",
        description=payload.description,
        story_bible=payload.story_bible,
        source_hash=payload.source_hash,
        source_filename=payload.source_filename,
        source_encoding=payload.source_encoding,
        genre=payload.genre,
        viewpoint=payload.viewpoint,
        style=payload.style,
        target_word_count=payload.target_word_count,
        must_happen=payload.must_happen,
        must_not_happen=payload.must_not_happen,
        hard_constraints=payload.hard_constraints,
        outline=payload.outline,
    )
    db.add(project)
    db.flush()
    start_mode = str(getattr(payload, "start_mode", None) or "setup").strip().lower()
    if start_mode == "assistant":  # early frontend preview compatibility
        start_mode = "setup"
    if start_mode not in {"blank", "setup", "import"}:
        raise HTTPException(status_code=422, detail="start_mode 必须为 blank、setup 或 import")
    first_chapter: Chapter | None = None
    if start_mode == "blank":
        first_chapter = Chapter(
            project_id=project.id,
            volume_number=1,
            chapter_number=1,
            sort_order=0,
            title=str(getattr(payload, "first_chapter_title", None) or "第一章 · 未命名稿纸"),
            status="draft",
            summary=None,
            summary_status="unprocessed",
            source_type="manual",
        )
        db.add(first_chapter)
        db.flush()
        project.current_chapter_id = first_chapter.id
        db.add(
            AuditLog(
                project=project,
                action="chapter.created",
                entity_type="chapter",
                entity_id=first_chapter.id,
                after_json={"origin": "project_wizard", "blank": True},
                actor_user_id=current_user.id,
            )
        )
    db.add(
        AuditLog(
            project=project,
            action="project.created",
            entity_type="project",
            entity_id=project.id,
            after_json={
                "start_mode": start_mode,
                "first_chapter_id": first_chapter.id if first_chapter else None,
            },
            actor_user_id=current_user.id,
        )
    )
    db.commit()
    db.refresh(project)
    return ProjectRead.model_validate(project)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectRead:
    return ProjectRead.model_validate(require_project(db, project_id, current_user))


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectRead:
    project = require_project(db, project_id, current_user)
    values = payload.model_dump(exclude_unset=True)
    # Rebuild state is an integrity gate, not a client-editable preference.
    values.pop("needs_rebuild", None)
    title = values.pop("title", None)
    if title is not None:
        values["name"] = title
    before = {key: getattr(project, key) for key in values if hasattr(project, key)}
    for key, value in values.items():
        if hasattr(project, key):
            setattr(project, key, value)
    project.updated_at = utcnow()
    db.add(
        AuditLog(
            project_id=project.id,
            action="project.updated",
            entity_type="project",
            entity_id=project.id,
            before_json=before,
            after_json={key: getattr(project, key) for key in values if hasattr(project, key)},
            actor_user_id=current_user.id,
        )
    )
    db.commit()
    db.refresh(project)
    return ProjectRead.model_validate(project)


@router.post("/{project_id}/memory/rebuild", response_model=ProjectRead)
def rebuild_project_memory(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectRead:
    """Promote edited chapter text and quarantine stale derived memory."""

    project = require_project(db, project_id, current_user)
    if not project.needs_rebuild:
        return ProjectRead.model_validate(project)
    chapters = db.scalars(
        select(Chapter).where(
            Chapter.project_id == project.id,
            Chapter.status == "needs_review",
        )
    ).all()
    promoted: list[str] = []
    for chapter in chapters:
        if chapter.current_revision_id:
            chapter.accepted_revision_id = chapter.current_revision_id
            chapter.status = "confirmed"
            chapter.confirmed_at = utcnow()
            chapter.summary = None
            chapter.summary_status = "current"
            promoted.append(chapter.id)
    project.needs_rebuild = False
    db.add(
        AuditLog(
            project_id=project.id,
            action="project.memory_rebuilt",
            entity_type="project",
            entity_id=project.id,
            after_json={
                "memory_epoch": project.memory_epoch,
                "promoted_chapter_ids": promoted,
                "stale_canon_kept_quarantined": True,
            },
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
        # The authoritative rebuild transaction is complete; derived search
        # will be refreshed again during the next application startup.
        pass
    db.refresh(project)
    return ProjectRead.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    project = require_project(db, project_id, current_user)
    quarantine = stage_storage_deletion(
        owner_id=current_user.id,
        project_id=project.id,
    )
    try:
        purge_project_search(db, owner_id=current_user.id, project_id=project.id)
        db.delete(project)
        db.commit()
    except Exception:
        db.rollback()
        quarantine.restore()
        raise
    quarantine.finalize()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/story-map", response_model=StoryMapResponse)
def story_map(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoryMapResponse:
    project = require_project(db, project_id, current_user)
    chapters = db.scalars(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order, Chapter.chapter_number)
    ).all()
    canon_items = db.scalars(
        select(CanonItem).where(CanonItem.project_id == project_id).order_by(CanonItem.created_at)
    ).all()
    events = db.scalars(
        select(TimelineEvent)
        .where(TimelineEvent.project_id == project_id)
        .order_by(TimelineEvent.sequence)
    ).all()
    threads = db.scalars(
        select(PlotThread)
        .where(PlotThread.project_id == project_id)
        .order_by(PlotThread.created_at)
    ).all()
    return StoryMapResponse(
        project=ProjectRead.model_validate(project),
        chapters=[chapter_payload(chapter) for chapter in chapters],
        canon_items=[CanonItemRead.model_validate(item) for item in canon_items],
        timeline_events=[TimelineEventRead.model_validate(event) for event in events],
        plot_threads=[PlotThreadRead.model_validate(thread) for thread in threads],
    )
