"""Project memory inspection, analysis, retry, and replayable progress."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db as db_module
from ..db import get_db
from ..models import (
    Chapter,
    Job,
    MemoryBuildArtifact,
    MemoryBuildRun,
    Project,
    StorySummary,
    User,
)
from ..schemas import MemoryBuildRunRead, StorySummaryRead
from ..security import get_current_user, user_id_of
from ..services.memory import create_memory_run, execute_memory_run, memory_run_snapshot
from . import require_project

router = APIRouter(prefix="/api", tags=["memory"])


class MemoryAnalyzePayload(BaseModel):
    scope: Literal["project", "chapter"] = "project"
    chapter_id: str | None = None


def _run_background(run_id: str) -> None:
    with db_module.SessionLocal() as session:
        execute_memory_run(session, run_id)


def _require_run(db: Session, run_id: str, user: User) -> MemoryBuildRun:
    run = db.scalar(
        select(MemoryBuildRun)
        .join(Project, Project.id == MemoryBuildRun.project_id)
        .where(MemoryBuildRun.id == run_id, Project.owner_id == user_id_of(user))
    )
    if run is None:
        raise HTTPException(status_code=404, detail="记忆任务不存在")
    return run


def _run_read(run: MemoryBuildRun) -> MemoryBuildRunRead:
    payload = MemoryBuildRunRead.model_validate(run).model_dump()
    payload.update(memory_run_snapshot(run))
    return MemoryBuildRunRead.model_validate(payload)


@router.get("/projects/{project_id}/memory")
def get_project_memory(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = require_project(db, project_id, current_user)
    summaries = db.scalars(
        select(StorySummary)
        .where(StorySummary.project_id == project.id)
        .order_by(StorySummary.scope, StorySummary.updated_at.desc())
    ).all()
    runs = db.scalars(
        select(MemoryBuildRun)
        .where(MemoryBuildRun.project_id == project.id)
        .order_by(MemoryBuildRun.created_at.desc())
        .limit(50)
    ).all()
    project_summary = next((item for item in summaries if item.scope == "project"), None)
    return {
        "project_id": project.id,
        "memory_epoch": project.memory_epoch,
        "auto_summary_enabled": bool(getattr(current_user, "auto_summary_enabled", True)),
        "project_summary": (
            StorySummaryRead.model_validate(project_summary).model_dump(mode="json")
            if project_summary
            else None
        ),
        "chapter_summaries": [
            StorySummaryRead.model_validate(item).model_dump(mode="json")
            for item in summaries
            if item.scope == "chapter"
        ],
        "runs": [_run_read(item).model_dump(mode="json") for item in runs],
    }


@router.post("/projects/{project_id}/memory/analyze")
def analyze_project_memory(
    project_id: str,
    payload: MemoryAnalyzePayload,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = require_project(db, project_id, current_user)
    chapter = None
    if payload.scope == "chapter":
        chapter_id = payload.chapter_id or project.current_chapter_id
        if not chapter_id:
            raise HTTPException(status_code=422, detail="当前项目还没有可分析章节")
        chapter = db.scalar(
            select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project.id)
        )
        if chapter is None:
            raise HTTPException(status_code=404, detail="章节不存在")
        if not chapter.accepted_revision_id:
            raise HTTPException(status_code=409, detail="请先完成本章，再整理正式记忆")
    try:
        created = create_memory_run(
            db,
            project,
            chapter=chapter,
            scope=payload.scope,
            actor_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if created.created or created.run.status in {"queued", "needs_retry"}:
        background.add_task(_run_background, str(created.run.id))
    return {**memory_run_snapshot(created.run), "created": created.created}


@router.get("/memory-runs/{run_id}", response_model=MemoryBuildRunRead)
def get_memory_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemoryBuildRunRead:
    return _run_read(_require_run(db, run_id, current_user))


@router.post("/memory-runs/{run_id}/retry", response_model=MemoryBuildRunRead)
def retry_memory_run(
    run_id: str,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemoryBuildRunRead:
    run = _require_run(db, run_id, current_user)
    if run.status not in {"failed", "needs_retry", "stale"}:
        raise HTTPException(status_code=409, detail="当前记忆任务不需要重试")
    run.status = "queued"
    run.stage = "queued"
    run.error = None
    run.finished_at = None
    job = db.scalar(
        select(Job).where(Job.project_id == run.project_id, Job.resource_id == run.id)
    )
    if job is not None:
        job.state = "queued"
        job.current_stage = "queued"
        job.last_error = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.attempts = int(job.attempts or 0) + 1
    db.commit()
    background.add_task(_run_background, str(run.id))
    db.refresh(run)
    return _run_read(run)


def _memory_events(
    run_id: str,
    owner_id: str,
    after: int,
    *,
    max_seconds: float = 60.0,
) -> Iterable[str]:
    started = time.monotonic()
    delivered = max(0, after)
    last_state = ""
    while time.monotonic() - started <= max_seconds:
        with db_module.SessionLocal() as session:
            run = session.scalar(
                select(MemoryBuildRun)
                .join(Project, Project.id == MemoryBuildRun.project_id)
                .where(MemoryBuildRun.id == run_id, Project.owner_id == owner_id)
            )
            if run is None:
                yield "event: error\ndata: {\"message\":\"记忆任务不存在\"}\n\n"
                return
            artifacts = session.scalars(
                select(MemoryBuildArtifact)
                .where(MemoryBuildArtifact.run_id == run.id)
                .order_by(MemoryBuildArtifact.created_at, MemoryBuildArtifact.id)
            ).all()
            for index, artifact in enumerate(artifacts, start=1):
                if index <= delivered:
                    continue
                payload = {
                    "sequence": index,
                    "stage": artifact.stage,
                    "content_hash": artifact.content_hash,
                    "created_at": artifact.created_at.isoformat(),
                }
                yield (
                    f"id: {index}\nevent: artifact\ndata: "
                    f"{json.dumps(payload, ensure_ascii=False)}\n\n"
                )
                delivered = index
            snapshot = memory_run_snapshot(run)
            encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
            if encoded != last_state:
                yield f"event: progress\ndata: {encoded}\n\n"
                last_state = encoded
            if run.status in {"current", "completed", "failed", "cancelled", "stale"}:
                return
        time.sleep(0.4)


@router.get("/memory-runs/{run_id}/events")
def memory_run_events(
    run_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    _require_run(db, run_id, current_user)
    try:
        after = int(last_event_id or 0)
    except ValueError:
        after = 0
    return StreamingResponse(
        _memory_events(run_id, current_user.id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router"]
