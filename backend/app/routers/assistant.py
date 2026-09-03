"""Conversation, event, and proposal endpoints for the setup assistant."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .. import db as db_module
from ..db import get_db
from ..models import (
    AgentConversation,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AuditLog,
    ChangeSet,
    Chapter,
    Job,
    MediaAsset,
    Project,
    Proposal,
    ProviderProfile,
    User,
    utcnow,
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
    ProposalUpdateRequest,
)
from ..security import get_current_user, user_id_of
from ..services.assistant import (
    ProposalNotEditableError,
    add_event,
    apply_proposal,
    cancel_assistant_run,
    conversation_title_from_content,
    create_conversation,
    create_message_run,
    reject_proposal,
    update_proposal,
)
from ..services.memory import create_memory_run, memory_run_snapshot
from ..services.providers import normalize_capabilities, parse_capability_bool
from . import require_project

router = APIRouter(prefix="/api", tags=["assistant"])
logger = logging.getLogger(__name__)


def _log_storage_failure(operation: str, exc: BaseException) -> None:
    """Record a safe server-side diagnostic without SQL or credential data."""

    logger.warning(
        "assistant storage failure",
        extra={
            "operation": operation,
            "error_type": type(exc).__name__,
            "pool_timeout": type(exc).__name__ in {"TimeoutError", "QueuePoolTimeout"},
        },
    )


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


def _effective_provider(
    db: Session,
    conversation: AgentConversation,
    user: User | None = None,
) -> tuple[str | None, str | None, bool, dict[str, bool]]:
    """Resolve the provider actually available to this conversation's tenant.

    Conversations normally persist the selected profile id.  Older rows may
    have no id, so resolve the creator's current account default in that case.
    Every lookup is owner-scoped; a profile id supplied by another tenant is
    treated as unavailable rather than exposed through the response.
    """

    # Route authentication has already established the tenant.  Prefer that
    # identity for profile ownership checks; the conversation creator is only
    # a fallback for internal callers that do not have a request user.
    owner_id = user_id_of(user) if user is not None else conversation.created_by_user_id
    provider_id = conversation.provider_profile_id
    if provider_id is None:
        provider_id = (
            user.default_provider_id
            if user is not None
            else db.scalar(select(User.default_provider_id).where(User.id == owner_id))
        )
    snapshot = (
        dict(conversation.provider_snapshot or {})
        if isinstance(conversation.provider_snapshot, dict)
        else {}
    )

    def selected_model(value: dict[str, Any]) -> str | None:
        roles = value.get("model_role_mapping")
        if not isinstance(roles, dict):
            return None
        model = roles.get("assistant") or roles.get("default") or roles.get("writer")
        if isinstance(model, dict):
            model = model.get("model")
        return str(model).strip() if model else None

    if not provider_id:
        return None, None, False, {"vision": False}
    profile = db.scalar(
        select(ProviderProfile).where(
            ProviderProfile.id == provider_id,
            ProviderProfile.owner_id == owner_id,
            ProviderProfile.enabled.is_(True),
            ProviderProfile.deleted_at.is_(None),
        )
    )
    if profile is None:
        snapshot_name = str(snapshot.get("name") or "").strip() or None
        return snapshot_name, selected_model(snapshot), False, {"vision": False}
    normalized = normalize_capabilities(profile.capabilities, strict=False)
    capabilities: dict[str, bool] = {}
    for key, value in normalized.items():
        try:
            capabilities[str(key)] = parse_capability_bool(value, field=f"capabilities.{key}")
        except ValueError:
            # The provider contract permits non-flag metadata in this JSON;
            # the assistant response advertises only effective boolean flags.
            continue
    capabilities.setdefault("vision", False)
    model = selected_model(snapshot) or selected_model(
        {"model_role_mapping": profile.model_role_mapping or {}}
    )
    return profile.name, model, True, capabilities


def _conversation_payload(
    conversation: AgentConversation,
    db: Session | None = None,
    user: User | None = None,
) -> AgentConversationRead:
    payload = AgentConversationRead.model_validate(conversation)
    if db is not None:
        if payload.title in {"故事设定助手", "和 Agent 一起写", "新的写作对话"}:
            first_user_message = db.scalar(
                select(AgentMessage.content)
                .where(
                    AgentMessage.conversation_id == conversation.id,
                    AgentMessage.role == "user",
                )
                .order_by(AgentMessage.sequence)
                .limit(1)
            )
            if first_user_message:
                payload.title = conversation_title_from_content(first_user_message)
        (
            provider_name,
            provider_model,
            provider_available,
            provider_capabilities,
        ) = _effective_provider(db, conversation, user)
        payload.provider_name = provider_name
        payload.provider_model = provider_model
        payload.provider_available = provider_available
        payload.provider_capabilities = provider_capabilities
    return payload


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
        return _conversation_payload(
            create_conversation(db, project, current_user, payload), db, current_user
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        _log_storage_failure("create_conversation", exc)
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "assistant_storage_unavailable",
                "message": "助手暂时无法创建会话，请稍后重试",
            },
        ) from exc


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
    return [_conversation_payload(row, db, current_user) for row in rows]


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
    return _conversation_payload(_conversation(db, project, conversation_id), db, current_user)


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
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "assistant_message_conflict",
                "message": "助手消息保存失败，请刷新后重试",
            },
        ) from exc
    except SQLAlchemyError as exc:
        _log_storage_failure("create_message", exc)
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "assistant_storage_unavailable",
                "message": "助手暂时无法保存消息，请稍后重试",
            },
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        conversation_payload = _conversation_payload(conversation, db, current_user)
    except SQLAlchemyError as exc:
        _log_storage_failure("read_conversation_after_message", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "assistant_storage_unavailable",
                "message": "助手暂时无法读取会话，请稍后重试",
            },
        ) from exc
    return {
        "created": created,
        "conversation": conversation_payload,
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
    next_attempt = int(job.attempts or 0) + 1
    max_attempts = max(1, int(job.max_attempts or 3))
    if next_attempt > max_attempts:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "assistant_retry_exhausted",
                "message": "助手运行已达到最大重试次数",
            },
        )
    run.status = "queued"
    run.stage = "queued"
    run.error = None
    run.finished_at = None
    job.state = "queued"
    job.current_stage = "queued"
    job.last_error = None
    job.lease_owner = None
    job.lease_expires_at = None
    run.input_snapshot = {**(run.input_snapshot or {}), "attempt": next_attempt}
    add_event(
        db,
        conversation,
        "run.stage",
        {
            "run_id": run.id,
            "status": run.status,
            "stage": run.stage,
            "retry": True,
            "attempt": next_attempt,
        },
        run_id=run.id,
    )
    db.commit()
    db.refresh(run)
    return AgentRunRead.model_validate(run)


@router.post(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/runs/{run_id}/cancel",
    response_model=AgentRunRead,
)
def cancel_run(
    project_id: str,
    conversation_id: str,
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AgentRunRead:
    project = require_project(db, project_id, current_user)
    conversation = _conversation(db, project, conversation_id)
    run = db.scalar(
        select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.conversation_id == conversation.id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="助手运行不存在")
    try:
        stopped = cancel_assistant_run(db, conversation, run, current_user)
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentRunRead.model_validate(stopped)


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


SSE_HEARTBEAT_SECONDS = 15.0
SSE_POLL_SECONDS = 0.35
SSE_MAX_SECONDS: float | None = None
_SSE_END = object()


def _wire_event_type(event_type: str) -> str:
    """Map persisted legacy names to the dotted Agent wire protocol.

    Early databases contain names such as ``message_delta``.  They remain
    valid rows and are replayable; only the SSE representation is upgraded so
    an existing client can reconnect across an application upgrade.
    """

    return {
        "run.started": "run.started",
        "run_started": "run.started",
        "run.stage": "run.stage",
        "run_stage": "run.stage",
        "run.completed": "run.completed",
        "run_completed": "run.completed",
        "run.failed": "run.failed",
        "run_failed": "run.failed",
        "run.cancelled": "run.cancelled",
        "run_cancelled": "run.cancelled",
        "message.delta": "message.delta",
        "message_delta": "message.delta",
        "message.replace": "message.replace",
        "message_replace": "message.replace",
        "message.created": "message.created",
        "message_created": "message.created",
        "message.started": "message.started",
        "message_started": "message.started",
        "message.completed": "message.completed",
        "message_completed": "message.completed",
        "proposal.created": "proposal.created",
        "proposal_created": "proposal.created",
        "proposal.patch": "proposal.patch",
        "proposal_patch": "proposal.patch",
        "proposal.ready": "proposal.ready",
        "proposal_ready": "proposal.ready",
    }.get(event_type, event_type)


def _safe_agent_error(value: Any) -> str:
    """Keep database implementation details out of a user-visible SSE frame."""

    text_value = str(value or "助手运行失败")
    lowered = text_value.lower()
    if any(
        marker in lowered
        for marker in (
            "sqlalchemy",
            "integrityerror",
            "operationalerror",
            "databaseerror",
            "statementerror",
            "(sqlite3.",
            "pymysql",
            "queuepool",
            "pool timeout",
            "pooltimeout",
        )
    ):
        return "助手事件保存失败，请稍后重试。"
    return text_value[:4000]


def _redact_storage_details(value: Any) -> Any:
    """Recursively redact SQLAlchemy/provider-driver diagnostics in failures."""

    if isinstance(value, dict):
        return {key: _redact_storage_details(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_storage_details(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(
            marker in lowered
            for marker in (
                "sqlalchemy",
                "integrityerror",
                "operationalerror",
                "databaseerror",
                "statementerror",
                "(sqlite3.",
                "pymysql",
                "queuepool",
                "pool timeout",
                "pooltimeout",
            )
        ):
            return "助手事件保存失败，请稍后重试。"
    return value


def _sse_frame(row: AgentEvent) -> str:
    """Encode one durable event without allowing payload keys to spoof metadata."""

    event_type = _wire_event_type(str(row.event_type))
    raw_payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    payload_json = dict(raw_payload)
    if event_type == "run.failed":
        payload_json = _redact_storage_details(payload_json)
        safe_error = _safe_agent_error(payload_json.get("message") or payload_json.get("error"))
        payload_json["error"] = safe_error
        payload_json["message"] = safe_error
    nested_proposal = payload_json.get("proposal")
    if not isinstance(nested_proposal, dict):
        nested_proposal = {}
    try:
        attempt = max(1, int(payload_json.get("attempt", nested_proposal.get("attempt", 1))))
    except (TypeError, ValueError):
        attempt = 1
    target = payload_json.get("target")
    if not isinstance(target, dict):
        target = nested_proposal.get("target")
    if not isinstance(target, dict):
        target = {}
    base_version_value = payload_json.get("base_version", nested_proposal.get("base_version"))
    if base_version_value is not None:
        try:
            base_version: int | None = int(base_version_value)
        except (TypeError, ValueError):
            base_version = None
    else:
        base_version = None
    # The database sequence is the replay cursor.  It is independent from
    # provider payload data so a model cannot spoof a reconnect position.
    cursor = str(row.sequence)
    payload: dict[str, Any] = {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "run_id": row.run_id,
        "sequence": row.sequence,
        "cursor": cursor,
        "attempt": attempt,
        "target": target,
        "base_version": base_version,
        "event_type": event_type,
        "type": event_type,
        "payload_json": payload_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    # Keep the historical flattened payload consumed by the original panel,
    # but restore reserved protocol fields after flattening untrusted JSON.
    payload.update(payload_json)
    payload.update(
        {
            "id": row.id,
            "conversation_id": row.conversation_id,
            "run_id": row.run_id,
            "sequence": row.sequence,
            "cursor": cursor,
            "attempt": attempt,
            "target": target,
            "base_version": base_version,
            "event_type": event_type,
            "type": event_type,
            "payload_json": payload_json,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
    )
    if event_type == "run.failed":
        payload["message"] = payload_json["message"]
        payload["error"] = payload_json["error"]
    return f"id: {row.sequence}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _event_cursor(
    db: Session,
    conversation_id: str,
    value: str | None,
    fallback: int,
) -> int:
    """Accept sequence cursors and UUID cursors emitted by older assistants."""

    if value is None:
        return max(0, fallback)
    candidate = value.strip()
    try:
        return max(0, int(candidate))
    except (AttributeError, TypeError, ValueError):
        pass
    if not candidate:
        return max(0, fallback)
    legacy_sequence = db.scalar(
        select(AgentEvent.sequence).where(
            AgentEvent.conversation_id == conversation_id,
            AgentEvent.id == candidate,
        )
    )
    return max(0, int(legacy_sequence or fallback))


def _sse_events(
    session_factory: Any,
    conversation_id: str,
    after: int,
    *,
    poll_seconds: float = SSE_POLL_SECONDS,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
    max_seconds: float | None = SSE_MAX_SECONDS,
) -> Iterator[str]:
    """Replay durable events with bounded polling and comment heartbeats.

    A new SQLAlchemy session is opened for every poll and immediately closed.
    The caller may wrap this iterator with an ASGI disconnect checker; keeping
    the database loop synchronous also preserves the public helper used by
    older tests and worker integrations.
    """

    last = max(0, int(after))
    started = time.monotonic()
    deadline = started + max_seconds if max_seconds is not None else None
    last_emit = started
    poll = max(0.01, float(poll_seconds))
    heartbeat = float(heartbeat_seconds)
    while deadline is None or time.monotonic() < deadline:
        try:
            with session_factory() as db:
                rows = _event_rows(db, conversation_id, last)
                frames: list[str] = []
                for row in rows:
                    last = row.sequence
                    last_emit = time.monotonic()
                    frames.append(_sse_frame(row))
        except SQLAlchemyError as exc:
            _log_storage_failure("stream_poll", exc)
            # Headers are already committed once a StreamingResponse starts;
            # terminate with a safe, id-less SSE error so clients can retry
            # from their last durable cursor without seeing driver details.
            error_payload = {
                "code": "assistant_storage_unavailable",
                "message": "助手事件暂时不可用，请稍后重试",
                "cursor": str(last),
                "attempt": 1,
                "target": {},
                "base_version": None,
            }
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
            return
        # Do not yield while the polling session is inside its context manager:
        # a slow client must never keep a database connection checked out.
        yield from frames
        if rows:
            continue
        now = time.monotonic()
        if heartbeat > 0 and now - last_emit >= heartbeat:
            # SSE comments are deliberately id-less: a heartbeat must never
            # advance Last-Event-ID or cause a replay cursor to skip data.
            yield ": heartbeat\n\n"
            last_emit = now
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(poll, remaining))
        else:
            time.sleep(poll)


async def _sse_events_async(
    session_factory: Any,
    conversation_id: str,
    after: int,
    *,
    request: Request | None = None,
    poll_seconds: float = SSE_POLL_SECONDS,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
    max_seconds: float | None = SSE_MAX_SECONDS,
) -> AsyncIterator[str]:
    """Production SSE loop using native async sleeps and short-lived sessions.

    The synchronous ``_sse_events`` helper remains available for older tests
    and integrations.  The HTTP endpoint uses this generator so cancellation
    and disconnects do not race a worker-thread ``next()`` call.
    """

    last = max(0, int(after))
    started = time.monotonic()
    deadline = started + max_seconds if max_seconds is not None else None
    last_emit = started
    poll = max(0.01, float(poll_seconds))
    heartbeat = float(heartbeat_seconds)
    while deadline is None or time.monotonic() < deadline:
        if request is not None and await request.is_disconnected():
            return
        try:
            with session_factory() as db:
                rows = _event_rows(db, conversation_id, last)
                frames: list[str] = []
                for row in rows:
                    last = row.sequence
                    last_emit = time.monotonic()
                    frames.append(_sse_frame(row))
        except SQLAlchemyError as exc:
            _log_storage_failure("stream_poll", exc)
            error_payload = {
                "code": "assistant_storage_unavailable",
                "message": "助手事件暂时不可用，请稍后重试",
                "cursor": str(last),
                "attempt": 1,
                "target": {},
                "base_version": None,
            }
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
            return
        # The session is closed before yielding, so a slow/cancelled client
        # cannot pin a database connection for the lifetime of the stream.
        for frame in frames:
            if request is not None and await request.is_disconnected():
                return
            yield frame
        if rows:
            continue
        now = time.monotonic()
        if heartbeat > 0 and now - last_emit >= heartbeat:
            if request is not None and await request.is_disconnected():
                return
            yield ": heartbeat\n\n"
            last_emit = now
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(poll, remaining))
        else:
            await asyncio.sleep(poll)


# Keep a stable identity so tests/integrations that monkeypatch the legacy
# synchronous helper still receive their finite iterator without changing the
# normal production path.
_DEFAULT_SYNC_SSE_EVENTS = _sse_events


def _next_sse_item(iterator: Iterator[str]) -> str | object:
    try:
        return next(iterator)
    except StopIteration:
        return _SSE_END


async def _stream_until_disconnect(request: Request, iterator: Any):
    """Consume a sync SSE iterator without retaining request-scoped resources."""

    # ``_sse_events`` is intentionally kept as a sync helper for compatibility
    # with integrations that call it directly.  ``to_thread`` keeps its SQLite
    # polling sleep off the event loop, while ``is_disconnected`` is checked
    # between polls and after every delivered frame.
    try:
        if hasattr(iterator, "__aiter__"):
            async for item in iterator:
                if await request.is_disconnected():
                    return
                yield item
            return
        sync_iterator = iter(iterator)
        while True:
            if await request.is_disconnected():
                return
            item = await asyncio.to_thread(_next_sse_item, sync_iterator)
            if item is _SSE_END:
                return
            yield item
            if await request.is_disconnected():
                return
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()


@router.get(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/events/stream",
    response_class=StreamingResponse,
)
def stream_assistant_events(
    project_id: str,
    conversation_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    try:
        project = require_project(db, project_id, current_user)
        _conversation(db, project, conversation_id)
        # Browsers usually reconnect with Last-Event-ID while API clients may
        # continue using the historical ?after= query parameter.  The header
        # is authoritative when it is a valid sequence number.
        effective_after = _event_cursor(db, conversation_id, last_event_id, after)
    except SQLAlchemyError as exc:
        _log_storage_failure("stream_validation", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "assistant_storage_unavailable",
                "message": "助手事件暂时不可用，请稍后重试",
            },
        ) from exc
    finally:
        # FastAPI finalises yield-dependencies only after a StreamingResponse
        # finishes.  Close this validation session now so a client that leaves
        # the stream open does not pin a request connection for its lifetime.
        db.close()
    if _sse_events is _DEFAULT_SYNC_SSE_EVENTS:
        # Normal HTTP traffic uses the cancellable native-async loop.  The
        # compatibility branch below is only for callers that replace the
        # historic synchronous helper with a finite test/integration stream.
        stream = _sse_events_async(
            db_module.SessionLocal,
            conversation_id,
            effective_after,
            request=request,
        )
    else:
        stream = _stream_until_disconnect(
            request,
            _sse_events(db_module.SessionLocal, conversation_id, effective_after),
        )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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


def _update(
    proposal_id: str,
    payload: ProposalUpdateRequest,
    current_user: User,
    db: Session,
) -> ProposalRead:
    proposal = _proposal(db, proposal_id, current_user)
    try:
        proposal = update_proposal(
            db,
            proposal,
            current_user,
            payload.patches,
            expected_version=payload.expected_version,
            expected_memory_epoch=payload.expected_memory_epoch,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "proposal_conflict", "message": str(exc)},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProposalNotEditableError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "proposal_not_editable", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "proposal_patch_invalid", "message": str(exc)},
        ) from exc
    return ProposalRead.model_validate(proposal)


@router.patch(
    "/projects/{project_id}/assistant/proposals/{proposal_id}",
    response_model=ProposalRead,
)
def update_project_proposal(
    project_id: str,
    proposal_id: str,
    payload: ProposalUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProposalRead:
    project = require_project(db, project_id, current_user)
    proposal = _proposal(db, proposal_id, current_user)
    if proposal.project_id != project.id:
        raise HTTPException(status_code=404, detail="提案不存在")
    return _update(proposal_id, payload, current_user, db)


@router.patch("/assistant/proposals/{proposal_id}", response_model=ProposalRead)
def update_proposal_direct(
    proposal_id: str,
    payload: ProposalUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProposalRead:
    return _update(proposal_id, payload, current_user, db)


@router.patch(
    "/projects/{project_id}/assistant/conversations/{conversation_id}/proposals/{proposal_id}",
    response_model=ProposalRead,
    include_in_schema=False,
)
def update_conversation_proposal(
    project_id: str,
    conversation_id: str,
    proposal_id: str,
    payload: ProposalUpdateRequest,
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
    return _update(proposal_id, payload, current_user, db)


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
    chapter_ids = {
        str(row.target_id or row.scope_chapter_id)
        for row in rows
        if row.operation in {"edit_chapter", "edit_chapter_selection"}
        and (row.target_id or row.scope_chapter_id)
    }
    chapter_order = {
        str(chapter.id): (int(chapter.sort_order or 0), int(chapter.chapter_number or 0))
        for chapter in db.scalars(
            select(Chapter).where(
                Chapter.project_id == project.id,
                Chapter.id.in_(chapter_ids),
            )
        ).all()
    } if chapter_ids else {}
    return sorted(
        rows,
        key=lambda row: (
            order.get(row.operation, 10),
            chapter_order.get(str(row.target_id or row.scope_chapter_id), (10**9, 10**9)),
            row.created_at,
        ),
    )


def _is_global_assistant_batch(db: Session, proposals: list[Proposal]) -> bool:
    run_ids = {
        str(row.change_set.source_id)
        for row in proposals
        if row.change_set is not None
        and row.change_set.source_type == "assistant"
        and row.change_set.source_id
    }
    if not run_ids:
        return False
    purposes = db.scalars(
        select(AgentConversation.purpose)
        .join(AgentRun, AgentRun.conversation_id == AgentConversation.id)
        .where(AgentRun.id.in_(run_ids))
        .distinct()
    ).all()
    return bool(purposes) and all(
        str(purpose or "").lower() in {"global", "global_story", "setup_global"}
        for purpose in purposes
    )


def _apply_batch_for_project(
    db: Session, project: Project, payload: ProposalBatchRequest, current_user: User
) -> dict[str, Any]:
    proposals = _project_proposals(db, project, payload.proposal_ids)
    global_batch = _is_global_assistant_batch(db, proposals)
    affected_chapter_ids = {
        str(proposal.target_id or proposal.scope_chapter_id)
        for proposal in proposals
        if proposal.operation in {"edit_chapter", "edit_chapter_selection"}
        and (proposal.target_id or proposal.scope_chapter_id)
    }
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
    memory_run = None
    if global_batch:
        chapters = (
            db.scalars(
                select(Chapter)
                .where(
                    Chapter.project_id == project.id,
                    Chapter.id.in_(affected_chapter_ids),
                )
                .order_by(Chapter.sort_order, Chapter.chapter_number)
                .with_for_update()
            ).all()
            if affected_chapter_ids
            else []
        )
        for chapter in chapters:
            if not chapter.current_revision_id:
                continue
            chapter.accepted_revision_id = chapter.current_revision_id
            chapter.status = "confirmed"
            chapter.confirmed_at = utcnow()
            chapter.summary_status = "unprocessed"
        if chapters:
            project.memory_epoch = int(project.memory_epoch or 0) + 1
        project.needs_rebuild = True
        db.add(
            AuditLog(
                project_id=project.id,
                actor_user_id=current_user.id,
                actor=current_user.username or current_user.email or current_user.id,
                action="assistant.global_diff_accepted",
                entity_type="project",
                entity_id=project.id,
                after_json={
                    "chapter_ids": [chapter.id for chapter in chapters],
                    "proposal_ids": [proposal.id for proposal in proposals],
                },
            )
        )
        created = create_memory_run(
            db,
            project,
            scope="project",
            actor_user_id=current_user.id,
            commit=False,
        )
        memory_run = created.run
        db.commit()
    result_payload = {
        "status": "applied",
        "project_id": project.id,
        "applied_count": len(applied),
        "proposals": applied,
    }
    if memory_run is not None:
        result_payload["memory_run"] = memory_run_snapshot(memory_run)
    return result_payload


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
    return _conversation_payload(
        _direct_conversation(db, conversation_id, current_user), db, current_user
    )
