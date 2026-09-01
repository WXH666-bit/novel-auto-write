"""Conversation, event, and proposal endpoints for the setup assistant."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import db as db_module
from ..db import get_db
from ..models import (
    AgentConversation,
    AgentEvent,
    AgentMessage,
    AgentRun,
    ChangeSet,
    Job,
    MediaAsset,
    Project,
    Proposal,
    User,
)
from ..schemas import (
    AgentConversationCreate,
    AgentConversationRead,
    AgentEventRead,
    AgentMessageCreate,
    AgentMessageRead,
    AgentRunRead,
    ChangeSetRead,
    ProposalApplyRequest,
    ProposalBatchRequest,
    ProposalRead,
    ProposalRejectRequest,
)
from ..security import get_current_user, user_id_of
from ..services.assistant import (
    apply_proposal,
    create_conversation,
    create_message_run,
    reject_proposal,
)
from . import require_project

router = APIRouter(prefix="/api", tags=["assistant"])


def _conversation(db: Session, project: Project, conversation_id: str) -> AgentConversation:
    conversation = db.scalar(
        select(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.project_id == project.id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="助手会话不存在")
    return conversation


def _direct_conversation(db: Session, conversation_id: str, user: User) -> AgentConversation:
    conversation = db.scalar(
        select(AgentConversation)
        .join(Project, Project.id == AgentConversation.project_id)
        .where(
            AgentConversation.id == conversation_id,
            Project.owner_id == user_id_of(user),
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="助手会话不存在")
    return conversation


def _proposal(db: Session, proposal_id: str, user: User) -> Proposal:
    proposal = db.scalar(
        select(Proposal)
        .join(Project, Project.id == Proposal.project_id)
        .where(Proposal.id == proposal_id, Project.owner_id == user_id_of(user))
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="提案不存在")
    return proposal


def _run_payload(run: AgentRun | None) -> AgentRunRead | None:
    return AgentRunRead.model_validate(run) if run is not None else None


def _message_payload(message: AgentMessage) -> AgentMessageRead:
    return AgentMessageRead.model_validate(message)


def _conversation_payload(conversation: AgentConversation) -> AgentConversationRead:
    return AgentConversationRead.model_validate(conversation)


def _verify_assets(
    db: Session, project: Project, user: User, asset_ids: list[str]
) -> None:
    if not asset_ids:
        return
    unique_ids = set(asset_ids)
    count = db.scalar(
        select(func.count(MediaAsset.id)).where(
            MediaAsset.id.in_(unique_ids),
            MediaAsset.project_id == project.id,
            MediaAsset.owner_id == user_id_of(user),
        )
    )
    if int(count or 0) != len(unique_ids):
        raise HTTPException(status_code=404, detail="对话引用的图片不存在或不属于当前项目")


@router.post(
    "/projects/{project_id}/assistant/conversations",
    response_model=AgentConversationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_assistant_conversation(
    project_id: str,
    payload: AgentConversationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentConversationRead:
    project = require_project(db, project_id, current_user)
    try:
        return _conversation_payload(create_conversation(db, project, current_user, payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/projects/{project_id}/assistant/conversations",
    response_model=list[AgentConversationRead],
)
def list_assistant_conversations(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AgentConversationRead]:
    project = require_project(db, project_id, current_user)
    rows = db.scalars(
        select(AgentConversation)
        .where(
            AgentConversation.project_id == project.id,
            AgentConversation.created_by_user_id == user_id_of(current_user),
        )
        .order_by(AgentConversation.updated_at.desc())
    ).all()
    return [_conversation_payload(row) for row in rows]


@router.get(
    "/projects/{project_id}/assistant/conversations/{conversation_id}",
    response_model=AgentConversationRead,
)
def get_assistant_conversation(
    project_id: str,
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentConversationRead:
    project = require_project(db, project_id, current_user)
    return _conversation_payload(_conversation(db, project, conversation_id))


@router.get(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/messages",
    response_model=list[AgentMessageRead],
)
def list_assistant_messages(
    project_id: str,
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AgentMessageRead]:
    project = require_project(db, project_id, current_user)
    conversation = _conversation(db, project, conversation_id)
    rows = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == conversation.id)
        .order_by(AgentMessage.sequence)
    ).all()
    return [_message_payload(row) for row in rows]


@router.post(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/messages",
    response_model=dict[str, Any],
    status_code=status.HTTP_202_ACCEPTED,
)
def post_assistant_message(
    project_id: str,
    conversation_id: str,
    payload: AgentMessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = require_project(db, project_id, current_user)
    conversation = db.scalar(
        select(AgentConversation)
        .where(
            AgentConversation.id == conversation_id,
            AgentConversation.project_id == project.id,
        )
        .with_for_update()
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="助手会话不存在")
    if payload.expected_version is not None and payload.expected_version != conversation.version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conversation_conflict",
                "message": "助手会话已在其他窗口更新",
                "expected_version": payload.expected_version,
                "actual_version": conversation.version,
            },
        )
    _verify_assets(db, project, current_user, payload.authorized_asset_ids)
    try:
        message, run, created = create_message_run(
            db,
            conversation,
            current_user,
            payload.content,
            idempotency_key=payload.idempotency_key,
            target=payload.target,
            context_snapshot=payload.context_snapshot,
            authorized_asset_ids=payload.authorized_asset_ids,
        )
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "created": created,
        "conversation": _conversation_payload(conversation),
        "message": _message_payload(message),
        "run": _run_payload(run),
    }


@router.get(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/runs",
    response_model=list[AgentRunRead],
)
def list_assistant_runs(
    project_id: str,
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AgentRunRead]:
    project = require_project(db, project_id, current_user)
    conversation = _conversation(db, project, conversation_id)
    rows = db.scalars(
        select(AgentRun).where(AgentRun.conversation_id == conversation.id).order_by(AgentRun.created_at)
    ).all()
    return [AgentRunRead.model_validate(row) for row in rows]


@router.post(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/runs/{run_id}/retry",
    response_model=AgentRunRead,
)
def retry_assistant_run(
    project_id: str,
    conversation_id: str,
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentRunRead:
    project = require_project(db, project_id, current_user)
    conversation = _conversation(db, project, conversation_id)
    run = db.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.conversation_id == conversation.id)
        .with_for_update()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="助手运行不存在")
    if run.status not in {"failed", "needs_retry"}:
        raise HTTPException(status_code=409, detail="当前助手运行不需要重试")
    job = db.scalar(
        select(Job).where(Job.resource_id == run.id, Job.kind == "assistant").with_for_update()
    )
    if job is None:
        raise HTTPException(status_code=409, detail="助手运行缺少持久化任务")
    run.status = "queued"
    run.stage = "queued"
    run.error = None
    run.finished_at = None
    job.state = "queued"
    job.current_stage = "queued"
    job.last_error = None
    job.lease_owner = None
    job.lease_expires_at = None
    db.commit()
    db.refresh(run)
    return AgentRunRead.model_validate(run)


def _event_rows(db: Session, conversation_id: str, after: int = 0) -> list[AgentEvent]:
    return db.scalars(
        select(AgentEvent)
        .where(AgentEvent.conversation_id == conversation_id, AgentEvent.sequence > after)
        .order_by(AgentEvent.sequence)
    ).all()


@router.get(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/events",
    response_model=list[AgentEventRead],
)
def list_assistant_events(
    project_id: str,
    conversation_id: str,
    after: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AgentEventRead]:
    project = require_project(db, project_id, current_user)
    conversation = _conversation(db, project, conversation_id)
    return [AgentEventRead.model_validate(row) for row in _event_rows(db, conversation.id, after)]


def _sse_events(session_factory: Any, conversation_id: str, after: int) -> Iterator[str]:
    last = after
    deadline = time.monotonic() + 60
    event_type_map = {
        "message.delta": "message_delta",
        "message_delta": "message_delta",
        "proposal.created": "proposal_created",
        "proposal_created": "proposal_created",
        "message.created": "status",
        "message_created": "status",
        "run.started": "status",
        "run_started": "status",
        "message.started": "status",
        "message_started": "status",
        "message.completed": "message_completed",
        "message_completed": "message_completed",
        "run.failed": "error",
        "run_failed": "error",
    }
    while time.monotonic() < deadline:
        with session_factory() as db:
            rows = _event_rows(db, conversation_id, last)
            for row in rows:
                last = row.sequence
                payload = {
                    "id": row.id,
                    "conversation_id": row.conversation_id,
                    "run_id": row.run_id,
                    "sequence": row.sequence,
                    "type": event_type_map.get(row.event_type, row.event_type),
                    "payload_json": row.payload_json,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                payload.update(row.payload_json or {})
                if payload["type"] == "error" and "message" not in payload:
                    payload["message"] = payload.get("error", "Agent 运行失败")
                yield f"id: {row.sequence}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
        if rows:
            continue
        time.sleep(0.35)


@router.get(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/events/stream",
    response_class=StreamingResponse,
)
def stream_assistant_events(
    project_id: str,
    conversation_id: str,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    project = require_project(db, project_id, current_user)
    _conversation(db, project, conversation_id)
    # Browsers usually reconnect with Last-Event-ID while API clients may
    # continue using the historical ?after= query parameter.  The header is
    # authoritative when it is a valid sequence number.
    effective_after = after
    if last_event_id is not None:
        try:
            effective_after = max(0, int(last_event_id))
        except ValueError:
            effective_after = after
    return StreamingResponse(
        _sse_events(db_module.SessionLocal, conversation_id, effective_after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/projects/{project_id}/assistant/proposals", response_model=list[ProposalRead])
def list_proposals(
    project_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    conversation_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProposalRead]:
    project = require_project(db, project_id, current_user)
    query = select(Proposal).where(Proposal.project_id == project.id)
    if conversation_id:
        _conversation(db, project, conversation_id)
        query = (
            query.join(ChangeSet, ChangeSet.id == Proposal.change_set_id)
            .join(AgentRun, AgentRun.id == ChangeSet.source_id)
            .where(AgentRun.conversation_id == conversation_id)
        )
    if status_filter:
        query = query.where(Proposal.status == status_filter)
    rows = db.scalars(query.order_by(Proposal.created_at.desc())).all()
    return [ProposalRead.model_validate(row) for row in rows]


@router.get("/projects/{project_id}/assistant/change-sets", response_model=list[ChangeSetRead])
def list_change_sets(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ChangeSetRead]:
    project = require_project(db, project_id, current_user)
    rows = db.scalars(
        select(ChangeSet).where(ChangeSet.project_id == project.id).order_by(ChangeSet.created_at.desc())
    ).all()
    return [ChangeSetRead.model_validate(row) for row in rows]


def _apply(
    proposal_id: str,
    payload: ProposalApplyRequest,
    current_user: User,
    db: Session,
) -> ProposalRead:
    proposal = _proposal(db, proposal_id, current_user)
    try:
        proposal = apply_proposal(
            db,
            proposal,
            current_user,
            expected_version=payload.expected_version,
            expected_memory_epoch=payload.expected_memory_epoch,
            reason=payload.reason,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "proposal_conflict", "message": str(exc)},
        ) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ProposalRead.model_validate(proposal)


@router.post("/assistant/proposals/{proposal_id}/apply", response_model=ProposalRead)
def apply_proposal_direct(
    proposal_id: str,
    payload: ProposalApplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProposalRead:
    return _apply(proposal_id, payload, current_user, db)


@router.post(
    "/projects/{project_id}/assistant/proposals/{proposal_id}/apply",
    response_model=ProposalRead,
)
def apply_project_proposal(
    project_id: str,
    proposal_id: str,
    payload: ProposalApplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProposalRead:
    project = require_project(db, project_id, current_user)
    proposal = _proposal(db, proposal_id, current_user)
    if proposal.project_id != project.id:
        raise HTTPException(status_code=404, detail="提案不存在")
    return _apply(proposal_id, payload, current_user, db)


@router.post(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/proposals/{proposal_id}/apply",
    response_model=ProposalRead,
    include_in_schema=False,
)
def apply_conversation_proposal(
    project_id: str,
    conversation_id: str,
    proposal_id: str,
    payload: ProposalApplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProposalRead:
    project = require_project(db, project_id, current_user)
    conversation = _conversation(db, project, conversation_id)
    proposal = _proposal(db, proposal_id, current_user)
    source_run = db.scalar(
        select(AgentRun).where(
            AgentRun.id == proposal.change_set.source_id,
            AgentRun.conversation_id == conversation.id,
        )
    )
    if proposal.project_id != project.id or source_run is None:
        # Assistant change sets are sourced from a run, so a proposal is not
        # considered part of an arbitrary conversation merely because the
        # caller guessed a valid project id.
        raise HTTPException(status_code=404, detail="提案不存在")
    return _apply(proposal_id, payload, current_user, db)


def _project_proposals(
    db: Session, project: Project, proposal_ids: list[str]
) -> list[Proposal]:
    unique_ids = list(dict.fromkeys(proposal_ids))
    rows = db.scalars(
        select(Proposal).where(
            Proposal.project_id == project.id,
            Proposal.id.in_(unique_ids),
        )
    ).all()
    by_id = {str(row.id): row for row in rows}
    if len(by_id) != len(unique_ids):
        raise HTTPException(status_code=404, detail="批量提案中存在不存在或不属于当前项目的记录")
    # Character cards must exist before relation edges can resolve their
    # automatically maintained graph nodes.
    order = {
        "create_character": 0,
        "upsert_character": 0,
        "update_character": 1,
        "upsert_graph_node": 2,
        "update_graph_node": 2,
        "upsert_graph_edge": 3,
        "update_graph_edge": 3,
    }
    return sorted(rows, key=lambda row: order.get(row.operation, 10))


def _apply_batch_for_project(
    db: Session, project: Project, payload: ProposalBatchRequest, current_user: User
) -> dict[str, Any]:
    proposals = _project_proposals(db, project, payload.proposal_ids)
    applied: list[ProposalRead] = []
    for index, proposal in enumerate(proposals):
        expected_epoch = payload.expected_memory_epoch if index == 0 else project.memory_epoch
        try:
            result = apply_proposal(
                db,
                proposal,
                current_user,
                expected_version=payload.expected_versions.get(str(proposal.id)),
                expected_memory_epoch=expected_epoch,
                reason=payload.reason,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "proposal_conflict",
                    "message": str(exc),
                    "applied_ids": [item.id for item in applied],
                    "conflict_id": proposal.id,
                },
            ) from exc
        except (LookupError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "proposal_invalid",
                    "message": str(exc),
                    "applied_ids": [item.id for item in applied],
                    "conflict_id": proposal.id,
                },
            ) from exc
        applied.append(ProposalRead.model_validate(result))
        db.refresh(project)
    return {
        "status": "applied",
        "project_id": project.id,
        "applied_count": len(applied),
        "proposals": applied,
    }


@router.post("/assistant/proposals/apply-batch", response_model=dict[str, Any])
def apply_proposals_batch(
    payload: ProposalBatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    projects = db.scalars(
        select(Project)
        .join(Proposal, Proposal.project_id == Project.id)
        .where(Proposal.id.in_(payload.proposal_ids), Project.owner_id == user_id_of(current_user))
        .distinct()
    ).all()
    if len(projects) != 1:
        raise HTTPException(status_code=404, detail="批量提案不存在或必须属于同一项目")
    return _apply_batch_for_project(db, projects[0], payload, current_user)


@router.post("/projects/{project_id}/assistant/proposals/apply-batch", response_model=dict[str, Any])
def apply_project_proposals_batch(
    project_id: str,
    payload: ProposalBatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = require_project(db, project_id, current_user)
    rows = db.scalars(
        select(Proposal).where(
            Proposal.project_id == project.id, Proposal.id.in_(payload.proposal_ids)
        )
    ).all()
    if len({str(row.id) for row in rows}) != len(set(payload.proposal_ids)):
        raise HTTPException(status_code=404, detail="批量提案中存在不存在的记录")
    return _apply_batch_for_project(db, project, payload, current_user)


def _reject(
    proposal_id: str,
    payload: ProposalRejectRequest,
    current_user: User,
    db: Session,
) -> ProposalRead:
    proposal = _proposal(db, proposal_id, current_user)
    try:
        proposal = reject_proposal(db, proposal, current_user, payload.reason)
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ProposalRead.model_validate(proposal)


@router.post("/assistant/proposals/{proposal_id}/reject", response_model=ProposalRead)
def reject_proposal_direct(
    proposal_id: str,
    payload: ProposalRejectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProposalRead:
    return _reject(proposal_id, payload, current_user, db)


@router.post(
    "/projects/{project_id}/assistant/proposals/{proposal_id}/reject",
    response_model=ProposalRead,
)
def reject_project_proposal(
    project_id: str,
    proposal_id: str,
    payload: ProposalRejectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProposalRead:
    project = require_project(db, project_id, current_user)
    proposal = _proposal(db, proposal_id, current_user)
    if proposal.project_id != project.id:
        raise HTTPException(status_code=404, detail="提案不存在")
    return _reject(proposal_id, payload, current_user, db)


@router.post(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/proposals/{proposal_id}/reject",
    response_model=ProposalRead,
    include_in_schema=False,
)
def reject_conversation_proposal(
    project_id: str,
    conversation_id: str,
    proposal_id: str,
    payload: ProposalRejectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProposalRead:
    project = require_project(db, project_id, current_user)
    conversation = _conversation(db, project, conversation_id)
    proposal = _proposal(db, proposal_id, current_user)
    source_run = db.scalar(
        select(AgentRun).where(
            AgentRun.id == proposal.change_set.source_id,
            AgentRun.conversation_id == conversation.id,
        )
    )
    if proposal.project_id != project.id or source_run is None:
        raise HTTPException(status_code=404, detail="提案不存在")
    return _reject(proposal_id, payload, current_user, db)


def _reject_batch_for_rows(
    db: Session, rows: list[Proposal], payload: ProposalBatchRequest, current_user: User
) -> dict[str, Any]:
    if len({str(row.id) for row in rows}) != len(set(payload.proposal_ids)):
        raise HTTPException(status_code=404, detail="批量提案中存在不存在或不属于当前账号的记录")
    rejected: list[ProposalRead] = []
    for proposal in rows:
        try:
            rejected.append(
                ProposalRead.model_validate(
                    reject_proposal(db, proposal, current_user, payload.reason)
                )
            )
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "rejected", "rejected_count": len(rejected), "proposals": rejected}


@router.post("/assistant/proposals/reject-batch", response_model=dict[str, Any])
def reject_proposals_batch(
    payload: ProposalBatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.scalars(
        select(Proposal)
        .join(Project, Project.id == Proposal.project_id)
        .where(
            Proposal.id.in_(payload.proposal_ids),
            Project.owner_id == user_id_of(current_user),
        )
    ).all()
    return _reject_batch_for_rows(db, rows, payload, current_user)


@router.post("/projects/{project_id}/assistant/proposals/reject-batch", response_model=dict[str, Any])
def reject_project_proposals_batch(
    project_id: str,
    payload: ProposalBatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = require_project(db, project_id, current_user)
    rows = db.scalars(
        select(Proposal).where(
            Proposal.project_id == project.id, Proposal.id.in_(payload.proposal_ids)
        )
    ).all()
    return _reject_batch_for_rows(db, rows, payload, current_user)


@router.get("/assistant/conversations/{conversation_id}", response_model=AgentConversationRead, include_in_schema=False)
def get_assistant_conversation_direct(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentConversationRead:
    return _conversation_payload(_direct_conversation(db, conversation_id, current_user))
