"""Account-level writing and derived-memory preferences."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, User
from ..schemas import PreferencesRead, PreferencesUpdate
from ..security import get_current_user, user_id_of

router = APIRouter(prefix="/api", tags=["preferences"])


def _read(user: User) -> PreferencesRead:
    return PreferencesRead(
        auto_summary_enabled=bool(getattr(user, "auto_summary_enabled", True)),
        preferences_version=int(getattr(user, "preferences_version", 1)),
    )


@router.get("/account/preferences", response_model=PreferencesRead)
@router.get("/account/preferences/", response_model=PreferencesRead, include_in_schema=False)
@router.get("/preferences", response_model=PreferencesRead, include_in_schema=False)
def get_preferences(
    current_user: User = Depends(get_current_user),
) -> PreferencesRead:
    return _read(current_user)


@router.patch("/account/preferences", response_model=PreferencesRead)
@router.patch("/account/preferences/", response_model=PreferencesRead, include_in_schema=False)
@router.patch("/preferences", response_model=PreferencesRead, include_in_schema=False)
def update_preferences(
    payload: PreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PreferencesRead:
    # Lock the account row where the database supports row locks.  SQLite
    # serialises the eventual write transaction and still receives the same
    # version check, so stale browser tabs get a deterministic 409.
    user = db.scalar(select(User).where(User.id == user_id_of(current_user)).with_for_update())
    if user is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    expected = payload.expected_version
    actual = int(getattr(user, "preferences_version", 1))
    if expected is not None and expected != actual:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "preferences_conflict",
                "message": "账户设置已被其他窗口更新，请刷新后重试",
                "expected_version": expected,
                "actual_version": actual,
            },
        )

    if payload.auto_summary_enabled is not None:
        user.auto_summary_enabled = bool(payload.auto_summary_enabled)
    user.preferences_version = actual + 1
    db.add(
        AuditLog(
            project_id=None,
            actor_user_id=user.id,
            actor=user.username or user.email or user.id,
            action="preferences.updated",
            entity_type="user_preferences",
            entity_id=user.id,
            after_json={
                "auto_summary_enabled": bool(user.auto_summary_enabled),
                "preferences_version": user.preferences_version,
            },
        )
    )
    db.commit()
    db.refresh(user)
    return _read(user)
