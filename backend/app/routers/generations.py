"""Generation job creation, recovery, retry, and progress streaming."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db as db_module
from ..db import get_db
from ..models import GenerationRun, Project, User
from ..schemas import GenerationRequest
from ..security import get_current_user, require_owned_provider
from ..services.common import ACTIVE_RUN_STATUSES
from ..services.generation import (
    GenerationBusy,
    IdempotencyConflict,
    ProviderRequired,
    RunNotFound,
    create_generation_run,
    execute_generation,
    recover_incomplete_runs,
    run_snapshot,
    sse_events,
)
from . import require_generation, require_project

router = APIRouter(prefix="/api", tags=["generations"])


def _run_background(run_id: str) -> None:
    db = db_module.SessionLocal()
    try:
        execute_generation(db, run_id)
    finally:
        db.close()


@router.post("/projects/{project_id}/generations")
def start_generation(
    project_id: str,
    payload: GenerationRequest,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = require_project(db, project_id, current_user)
    if payload.provider_id:
        require_owned_provider(db, payload.provider_id, current_user)
    try:
        result = create_generation_run(db, project, payload)
    except GenerationBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProviderRequired as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "provider_required", "message": str(exc)},
        ) from exc
    except (ValueError, RunNotFound) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result.created:
        background.add_task(_run_background, str(result.run.id))
    return {**run_snapshot(result.run), "created": result.created}


@router.post("/projects/{project_id}/generate")
def start_generation_alias(
    project_id: str,
    payload: GenerationRequest,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return start_generation(project_id, payload, background, current_user, db)


@router.get("/generations/{run_id}")
def get_generation(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = require_generation(db, run_id, current_user)
    return run_snapshot(run)


@router.get("/projects/{project_id}/generations/latest")
def latest_generation(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_project(db, project_id, current_user)
    run = db.scalar(
        select(GenerationRun)
        .where(GenerationRun.project_id == project_id)
        .order_by(GenerationRun.started_at.desc())
    )
    if run is None:
        raise HTTPException(status_code=404, detail="项目还没有生成任务")
    return run_snapshot(run)


@router.get("/generations/{run_id}/events")
def generation_events(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    require_generation(db, run_id, current_user)
    # The generator opens short-lived sessions per poll so a disconnected
    # browser never holds a database connection.
    return StreamingResponse(
        sse_events(db_module.SessionLocal, run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/generations/{run_id}/retry")
def retry_generation(
    run_id: str,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = require_generation(db, run_id, current_user)
    if run.status not in {"needs_retry", "failed"}:
        raise HTTPException(status_code=409, detail="当前任务不需要重试")
    db.scalar(select(Project.id).where(Project.id == run.project_id).with_for_update())
    other_active = db.scalar(
        select(GenerationRun.id)
        .where(
            GenerationRun.project_id == run.project_id,
            GenerationRun.id != run.id,
            GenerationRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .with_for_update()
    )
    if other_active is not None:
        raise HTTPException(status_code=409, detail="该项目已有其他活动生成任务")
    run.status = "queued"
    run.stage = run.stage or "queued"
    run.error = None
    from ..models import Job

    job = db.scalar(
        select(Job).where(
            Job.project_id == run.project_id,
            Job.idempotency_key == run.idempotency_key,
        )
    )
    if job is not None:
        job.state = "queued"
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error = None
    db.commit()
    background.add_task(_run_background, str(run.id))
    return run_snapshot(run)


@router.post("/generations/recover")
def recover_generations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    return {"recovered": recover_incomplete_runs(db, owner_id=current_user.id)}
