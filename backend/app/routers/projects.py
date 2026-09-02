"""Project and story-map endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, rebuild_search_index
from ..models import (
    AgentConversation,
    AgentRun,
    AuditLog,
    CanonItem,
    ChangeSet,
    Chapter,
    GenerationRun,
    Job,
    MemoryBuildRun,
    PlotThread,
    Project,
    Proposal,
    ReviewBundle,
    TimelineEvent,
    User,
    utcnow,
)
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

_ATTENTION_RETRY_STATUSES = frozenset({"failed", "needs_retry"})
_ATTENTION_INFRA_ERROR_MARKERS = (
    "sqlalchemy",
    "queuepool",
    "sqlite3",
    "pymysql",
    "integrityerror",
    "operationalerror",
    "programmingerror",
    "interfaceerror",
    "dbapi",
    "traceback",
    "stack trace",
    "database is locked",
    "no such table",
    "connection pool",
    "deadlock",
    "foreign key constraint",
    "duplicate entry",
    "connection refused",
)


def _attention_time(value: Any) -> datetime:
    """Return a comparable UTC timestamp for mixed SQLite/MySQL values."""

    if not isinstance(value, datetime):
        return datetime.min.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _attention_iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _attention_safe_detail(value: Any) -> str | None:
    """Keep implementation/database diagnostics out of user-facing JSON."""

    if not value:
        return None
    detail = str(value).strip()
    if not detail:
        return None
    lowered = detail.casefold()
    if any(marker in lowered for marker in _ATTENTION_INFRA_ERROR_MARKERS):
        return "任务未完成，可以重试"
    return detail[:2000]


def _attention_item(
    *,
    item_id: Any,
    kind: str,
    status: Any,
    title: str,
    detail: Any = None,
    chapter_id: Any = None,
    conversation_id: Any = None,
    run_id: Any = None,
    task_type: Any = None,
    job_id: Any = None,
    target_type: Any = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": str(item_id),
        "kind": kind,
        "status": str(status or ""),
        "title": title,
        "detail": _attention_safe_detail(detail),
        "chapter_id": str(chapter_id) if chapter_id else None,
        "conversation_id": str(conversation_id) if conversation_id else None,
        "run_id": str(run_id) if run_id else None,
        "task_type": str(task_type) if task_type else None,
        "job_id": str(job_id) if job_id else None,
        "target_type": str(target_type) if target_type else None,
        "created_at": _attention_iso(created_at),
    }


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


@router.get("/{project_id}/attention")
def project_attention(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the actionable work queue for one tenant-owned project.

    A review bundle is the source of truth for prose review.  ``pending`` is
    the first review pass and ``needs_review`` is a recheck after an edit or a
    failed audit; resolved and stale bundles are intentionally excluded.  A
    failed generic Job is merged with its linked run whenever possible so one
    outage does not show up twice in the queue.
    """

    project = require_project(db, project_id, current_user)
    chapters = db.scalars(select(Chapter).where(Chapter.project_id == project.id)).all()
    chapter_titles = {str(chapter.id): str(chapter.title or "未命名章节") for chapter in chapters}
    generation_runs = db.scalars(
        select(GenerationRun).where(GenerationRun.project_id == project.id)
    ).all()
    generation_run_by_id = {str(run.id): run for run in generation_runs}

    ordered: list[tuple[datetime, dict[str, Any]]] = []
    review_count = 0
    recheck_count = 0

    review_bundles = db.scalars(
        select(ReviewBundle)
        .where(
            ReviewBundle.project_id == project.id,
            ReviewBundle.status.in_(("pending", "needs_review")),
        )
        .order_by(ReviewBundle.created_at.desc(), ReviewBundle.id.desc())
    ).all()
    for bundle in review_bundles:
        status_value = str(bundle.status or "")
        is_recheck = status_value == "needs_review"
        chapter_id = bundle.chapter_id
        safe_chapter_id = chapter_id if chapter_id and str(chapter_id) in chapter_titles else None
        chapter_title = chapter_titles.get(str(safe_chapter_id)) if safe_chapter_id else None
        issues = getattr(bundle, "audit_issues", None)
        issue_count = len(issues) if isinstance(issues, list) else 0
        detail = f"{issue_count} 项审查提示" if issue_count else "等待完成审核"
        ordered.append(
            (
                _attention_time(bundle.created_at),
                _attention_item(
                    item_id=bundle.id,
                    kind="recheck" if is_recheck else "review",
                    status=status_value,
                    title=("待复核：" if is_recheck else "待审核：")
                    + (chapter_title or "审核包"),
                    detail=detail,
                    chapter_id=safe_chapter_id,
                    run_id=(
                        bundle.generation_run_id
                        if bundle.generation_run_id
                        and str(bundle.generation_run_id) in generation_run_by_id
                        else None
                    ),
                    task_type="review",
                    created_at=bundle.created_at,
                ),
            )
        )
        if is_recheck:
            recheck_count += 1
        else:
            review_count += 1

    change_sets = db.scalars(
        select(ChangeSet).where(ChangeSet.project_id == project.id)
    ).all()
    change_set_by_id = {str(change_set.id): change_set for change_set in change_sets}

    # Agent runs provide the optional conversation link for assistant-created
    # proposals and are also one of the durable run types that may need a
    # retry.  Loading the project-scoped rows once keeps all lookups tenant
    # constrained and avoids N+1 queries while assembling the mixed queue.
    agent_runs = db.scalars(
        select(AgentRun).where(AgentRun.project_id == project.id)
    ).all()
    agent_run_by_id = {str(run.id): run for run in agent_runs}
    conversations = db.scalars(
        select(AgentConversation).where(AgentConversation.project_id == project.id)
    ).all()
    conversation_ids = {str(conversation.id) for conversation in conversations}

    proposal_rows = db.scalars(
        select(Proposal)
        .where(Proposal.project_id == project.id, Proposal.status == "proposed")
        .order_by(Proposal.created_at.desc(), Proposal.id.desc())
    ).all()
    proposal_count = len(proposal_rows)
    for proposal in proposal_rows:
        target_type = str(proposal.target_type or "").strip().lower()
        chapter_id = (
            proposal.target_id
            if target_type in {"chapter", "chapter_revision"}
            and proposal.target_id
            and str(proposal.target_id) in chapter_titles
            else None
        )
        change_set = change_set_by_id.get(str(proposal.change_set_id))
        source_type = str(getattr(change_set, "source_type", "") or "").strip().lower()
        source_run_id = str(change_set.source_id) if change_set and change_set.source_id else ""
        source_run = agent_run_by_id.get(source_run_id) if source_type == "assistant" else None
        generation_source_run = (
            generation_run_by_id.get(source_run_id) if source_type == "generation" else None
        )
        safe_source_run_id = (
            source_run.id
            if source_run is not None
            else generation_source_run.id
            if generation_source_run is not None
            else None
        )
        safe_conversation_id = (
            source_run.conversation_id
            if source_run is not None and str(source_run.conversation_id) in conversation_ids
            else None
        )
        task_type = (
            "assistant"
            if source_type == "assistant"
            else "generation"
            if source_type == "generation"
            else "memory"
        )
        ordered.append(
            (
                _attention_time(proposal.created_at),
                _attention_item(
                    item_id=proposal.id,
                    kind="proposal",
                    status=proposal.status,
                    title="待处理设定提案",
                    detail=proposal.reason or proposal.operation,
                    chapter_id=chapter_id,
                    conversation_id=safe_conversation_id,
                    run_id=safe_source_run_id,
                    task_type=task_type,
                    target_type=proposal.target_type,
                    created_at=proposal.created_at,
                ),
            )
        )

    memory_runs = db.scalars(
        select(MemoryBuildRun).where(MemoryBuildRun.project_id == project.id)
    ).all()
    all_runs: dict[str, Any] = {
        **{str(run.id): run for run in generation_runs},
        **{str(run.id): run for run in memory_runs},
        **agent_run_by_id,
    }
    run_kind = {
        **{str(run.id): "generation" for run in generation_runs},
        **{str(run.id): "memory" for run in memory_runs},
        **{str(run.id): "assistant" for run in agent_runs},
    }
    run_by_kind_idempotency = {
        (kind, str(getattr(run, "idempotency_key", ""))): run_id
        for run_id, kind in run_kind.items()
        for run in (all_runs[run_id],)
        if getattr(run, "idempotency_key", None)
    }
    run_by_job_id = {
        str(run.job_id): str(run.id)
        for run in (*generation_runs, *agent_runs)
        if getattr(run, "job_id", None)
    }

    retry_items: dict[str, tuple[datetime, dict[str, Any]]] = {}

    def add_retry_run(run: Any, kind: str) -> None:
        status_value = str(getattr(run, "status", "") or "")
        if status_value not in _ATTENTION_RETRY_STATUSES:
            return
        run_id = str(run.id)
        timestamp = _attention_time(
            getattr(run, "finished_at", None)
            or getattr(run, "started_at", None)
            or getattr(run, "created_at", None)
        )
        title = {
            "generation": "生成任务需要重试",
            "memory": "记忆整理需要重试",
            "assistant": "助手任务需要重试",
        }.get(kind, "后台任务需要重试")
        chapter_id = getattr(run, "chapter_id", None)
        if chapter_id and str(chapter_id) not in chapter_titles:
            chapter_id = None
        item = _attention_item(
            item_id=run.id,
            kind="retry",
            status=status_value,
            title=title,
            detail=getattr(run, "error", None),
            chapter_id=chapter_id,
            conversation_id=(
                getattr(run, "conversation_id", None)
                if str(getattr(run, "conversation_id", "")) in conversation_ids
                else None
            ),
            run_id=run.id,
            task_type=kind,
            job_id=getattr(run, "job_id", None),
            created_at=(
                getattr(run, "finished_at", None)
                or getattr(run, "started_at", None)
                or getattr(run, "created_at", None)
            ),
        )
        retry_items[run_id] = (timestamp, item)

    for run in generation_runs:
        add_retry_run(run, "generation")
    for run in memory_runs:
        add_retry_run(run, "memory")
    for run in agent_runs:
        add_retry_run(run, "assistant")

    jobs = db.scalars(
        select(Job)
        .where(
            Job.project_id == project.id,
            Job.state.in_(tuple(_ATTENTION_RETRY_STATUSES)),
        )
        .order_by(Job.updated_at.desc(), Job.id.desc())
    ).all()
    for job in jobs:
        job_id = str(job.id)
        linked_run_id = None
        if job.resource_id and str(job.resource_id) in all_runs:
            linked_run_id = str(job.resource_id)
        elif job_id in run_by_job_id:
            linked_run_id = run_by_job_id[job_id]
        else:
            job_kind = str(job.kind or "generation")
            linked_run_id = run_by_kind_idempotency.get(
                (job_kind, str(job.idempotency_key or ""))
            )

        # A failed Job and its failed/needs_retry run represent one retry.  If
        # the run itself completed but the durable Job did not, retain the Job
        # as the actionable source while still exposing the run linkage.
        key = linked_run_id or f"job:{job_id}"
        current = retry_items.get(key)
        job_timestamp = _attention_time(getattr(job, "updated_at", None) or job.created_at)
        if current is not None:
            current_timestamp, current_item = current
            current_status = str(current_item.get("status") or "")
            merged_status = (
                "needs_retry"
                if "needs_retry" in {current_status, str(job.state or "")}
                else "failed"
            )
            if job_timestamp >= current_timestamp:
                current_item["created_at"] = _attention_iso(
                    getattr(job, "updated_at", None) or job.created_at
                )
                current_timestamp = job_timestamp
            current_item["status"] = merged_status
            if job.last_error:
                current_item["detail"] = _attention_safe_detail(job.last_error)
            current_item["task_type"] = run_kind.get(linked_run_id or "") or str(
                job.kind or current_item.get("task_type") or "generation"
            )
            current_item["job_id"] = job.id
            retry_items[key] = (current_timestamp, current_item)
            continue
        # A Job without a retryable public run has no safe user action.  It is
        # retained for diagnostics/recovery, but must not become a dead button
        # in the user-facing attention drawer.

    ordered.extend(retry_items.values())
    ordered.sort(key=lambda entry: (entry[0], str(entry[1]["id"])), reverse=True)
    items = [item for _timestamp, item in ordered]
    retry_count = len(retry_items)
    return {
        "total": len(items),
        "reviews": review_count,
        "rechecks": recheck_count,
        "proposals": proposal_count,
        "retries": retry_count,
        "items": items,
    }


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
