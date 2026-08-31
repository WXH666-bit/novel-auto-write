"""Generation job creation, recovery, retry, and progress streaming."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db as db_module
from ..db import get_db
from ..models import GenerationRun, Project
from ..schemas import GenerationRequest
from ..services.generation import (
    GenerationBusy,
    IdempotencyConflict,
    RunNotFound,
    create_generation_run,
    execute_generation,
    recover_incomplete_runs,
    run_snapshot,
    sse_events,
)

router = APIRouter(prefix="/api", tags=["generations"])


def _project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


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
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _project(db, project_id)
    try:
        result = create_generation_run(db, project, payload)
    except GenerationBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return start_generation(project_id, payload, background, db)


@router.get("/generations/{run_id}")
def get_generation(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="生成任务不存在")
    return run_snapshot(run)


@router.get("/projects/{project_id}/generations/latest")
def latest_generation(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    _project(db, project_id)
    run = db.scalar(
        select(GenerationRun)
        .where(GenerationRun.project_id == project_id)
        .order_by(GenerationRun.started_at.desc())
    )
    if run is None:
        raise HTTPException(status_code=404, detail="项目还没有生成任务")
    return run_snapshot(run)


@router.get("/generations/{run_id}/events")
def generation_events(run_id: str) -> StreamingResponse:
    # The generator opens short-lived sessions per poll so a disconnected
    # browser never holds a database connection.
    return StreamingResponse(
        sse_events(db_module.SessionLocal, run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/generations/{run_id}/retry")
def retry_generation(
    run_id: str, background: BackgroundTasks, db: Session = Depends(get_db)
) -> dict[str, Any]:
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="生成任务不存在")
    if run.status not in {"needs_retry", "failed"}:
        raise HTTPException(status_code=409, detail="当前任务不需要重试")
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
def recover_generations(db: Session = Depends(get_db)) -> dict[str, int]:
    return {"recovered": recover_incomplete_runs(db)}
