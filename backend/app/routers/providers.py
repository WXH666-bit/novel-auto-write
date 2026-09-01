"""Tenant-scoped Provider profiles and credential-manager endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, ProviderProfile, User, new_id
from ..security import get_current_user, user_id_of
from ..services.providers import (
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_ANTHROPIC_VERSION,
    SUPPORTED_PROTOCOLS,
    ProviderError,
    delete_api_key,
    get_api_key,
    provider_for,
    set_api_key,
    validate_provider_url,
)

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str | None = None
    protocol: str = "chat_completions"
    api_version: str | None = Field(default=None, max_length=80)
    max_output_tokens: int | None = Field(default=None, ge=1)
    anthropic_workspace_id: str | None = Field(default=None, max_length=255)
    model_role_mapping: dict[str, Any] = Field(default_factory=dict)
    context_length: int = Field(default=8192, ge=1)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    # Empty string is accepted from edit forms to mean "keep the saved key".
    api_key: str | None = None
    # Friendly aliases retained for the existing writing-desk client.
    default_model: str | None = None
    model_roles: dict[str, str] = Field(default_factory=dict)
    timeout_ms: int | None = Field(default=None, ge=1000, le=3_600_000)

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized not in SUPPORTED_PROTOCOLS:
            raise ValueError("protocol 必须是 chat_completions、responses 或 anthropic_messages")
        return normalized


class ProviderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = None
    protocol: str | None = None
    api_version: str | None = Field(default=None, max_length=80)
    max_output_tokens: int | None = Field(default=None, ge=1)
    anthropic_workspace_id: str | None = Field(default=None, max_length=255)
    model_role_mapping: dict[str, Any] | None = None
    context_length: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    capabilities: dict[str, Any] | None = None
    enabled: bool | None = None
    # Empty string is accepted from edit forms to mean "keep the saved key".
    api_key: str | None = None
    default_model: str | None = None
    model_roles: dict[str, str] | None = None
    timeout_ms: int | None = Field(default=None, ge=1000, le=3_600_000)


class APIKeyPayload(BaseModel):
    api_key: str = Field(min_length=1, max_length=4096)


def _role_mapping(payload: ProviderPayload | ProviderPatch, current: dict[str, Any] | None = None) -> dict[str, Any]:
    roles = dict(current or {})
    incoming = getattr(payload, "model_role_mapping", None)
    if incoming is not None:
        roles.update(incoming)
    aliases = getattr(payload, "model_roles", None)
    if aliases is not None:
        roles.update(aliases)
    default_model = getattr(payload, "default_model", None)
    if default_model:
        roles["default"] = default_model
        roles.setdefault("writer", default_model)
    return roles


def _base_url_for(protocol: str, value: str | None) -> str:
    if value and value.strip():
        return value.strip().rstrip("/")
    if protocol == "anthropic_messages":
        return DEFAULT_ANTHROPIC_BASE_URL
    return "https://api.openai.com/v1"


def _owner(user: User) -> str:
    return user_id_of(user)


def _profile_query(db: Session, provider_id: str, user: User) -> ProviderProfile | None:
    return db.scalar(
        select(ProviderProfile).where(
            ProviderProfile.id == provider_id,
            ProviderProfile.owner_id == _owner(user),
            ProviderProfile.deleted_at.is_(None),
        )
    )


def _required_profile(db: Session, provider_id: str, user: User) -> ProviderProfile:
    profile = _profile_query(db, provider_id, user)
    if profile is None:
        # Cross-tenant IDs intentionally reveal nothing beyond a 404.
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return profile


def _public(profile: ProviderProfile) -> dict[str, Any]:
    key_set = bool(get_api_key(profile))
    roles = dict(profile.model_role_mapping or {})
    return {
        "id": profile.id,
        "name": profile.name,
        "base_url": profile.base_url,
        "protocol": profile.protocol,
        "api_version": profile.api_version,
        "max_output_tokens": profile.max_output_tokens,
        "anthropic_workspace_id": profile.anthropic_workspace_id,
        "config_version": profile.config_version,
        "model_role_mapping": roles,
        "model_roles": roles,
        "default_model": str(roles.get("default") or roles.get("writer") or ""),
        "context_length": profile.context_length,
        "timeout_seconds": profile.timeout_seconds,
        "timeout_ms": profile.timeout_seconds * 1000,
        "capabilities": profile.capabilities or {},
        "enabled": profile.enabled,
        "has_api_key": key_set,
        "api_key_set": key_set,
        "is_default": False,
    }


def _mark_default(result: dict[str, Any], profile: ProviderProfile, user: User) -> dict[str, Any]:
    result["is_default"] = getattr(user, "default_provider_id", None) == profile.id
    return result


def _audit(
    db: Session,
    user: User,
    action: str,
    profile: ProviderProfile,
    *,
    after: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            action=action,
            entity_type="provider_profile",
            entity_id=profile.id,
            actor_user_id=_owner(user),
            actor=str(getattr(user, "email", "user")),
            after_json=after,
        )
    )


def _apply_payload(profile: ProviderProfile, payload: ProviderPayload | ProviderPatch) -> None:
    protocol = getattr(payload, "protocol", None) or profile.protocol
    protocol = protocol.lower().strip()
    if protocol not in SUPPORTED_PROTOCOLS:
        raise HTTPException(status_code=422, detail="不支持的 Provider 协议")
    if getattr(payload, "name", None) is not None:
        profile.name = payload.name  # type: ignore[union-attr]
    if getattr(payload, "base_url", None) is not None or profile.base_url is None:
        profile.base_url = _base_url_for(protocol, getattr(payload, "base_url", None))
    elif protocol != profile.protocol and not getattr(payload, "base_url", None):
        profile.base_url = _base_url_for(protocol, None)
    profile.protocol = protocol
    if getattr(payload, "api_version", None) is not None:
        profile.api_version = payload.api_version  # type: ignore[union-attr]
    elif protocol == "anthropic_messages" and not profile.api_version:
        profile.api_version = DEFAULT_ANTHROPIC_VERSION
    if getattr(payload, "max_output_tokens", None) is not None:
        profile.max_output_tokens = payload.max_output_tokens  # type: ignore[union-attr]
    if getattr(payload, "anthropic_workspace_id", None) is not None:
        profile.anthropic_workspace_id = payload.anthropic_workspace_id  # type: ignore[union-attr]
    if getattr(payload, "model_role_mapping", None) is not None or getattr(payload, "model_roles", None) is not None or getattr(payload, "default_model", None):
        profile.model_role_mapping = _role_mapping(payload, dict(profile.model_role_mapping or {}))
    if getattr(payload, "context_length", None) is not None:
        profile.context_length = payload.context_length  # type: ignore[union-attr]
    timeout_ms = getattr(payload, "timeout_ms", None)
    timeout_seconds = getattr(payload, "timeout_seconds", None)
    if timeout_ms is not None:
        profile.timeout_seconds = max(1, int(timeout_ms / 1000))
    elif timeout_seconds is not None:
        profile.timeout_seconds = timeout_seconds
    if getattr(payload, "capabilities", None) is not None:
        profile.capabilities = payload.capabilities  # type: ignore[union-attr]
    if getattr(payload, "enabled", None) is not None:
        profile.enabled = payload.enabled  # type: ignore[union-attr]
    validate_provider_url(profile.base_url)


def _set_config_version(profile: ProviderProfile) -> None:
    profile.config_version = max(1, int(profile.config_version or 0) + 1)


def _restore_api_key(profile: ProviderProfile, previous: str | None) -> None:
    """Compensate an external credential mutation after a DB rollback."""

    if previous:
        set_api_key(profile, previous)
    else:
        delete_api_key(profile)


@router.get("")
def list_providers(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    profiles = db.scalars(
        select(ProviderProfile)
        .where(ProviderProfile.owner_id == _owner(user), ProviderProfile.deleted_at.is_(None))
        .order_by(ProviderProfile.created_at.asc())
    ).all()
    return [_mark_default(_public(item), item, user) for item in profiles]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: ProviderPayload,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    base_url = _base_url_for(payload.protocol, payload.base_url)
    try:
        validate_provider_url(base_url)
        profile = ProviderProfile(
            owner_id=_owner(user),
            name=payload.name,
            base_url=base_url,
            protocol=payload.protocol,
            api_version=(payload.api_version or (DEFAULT_ANTHROPIC_VERSION if payload.protocol == "anthropic_messages" else None)),
            max_output_tokens=payload.max_output_tokens,
            anthropic_workspace_id=payload.anthropic_workspace_id,
            model_role_mapping=_role_mapping(payload),
            context_length=payload.context_length,
            timeout_seconds=max(1, int((payload.timeout_ms or payload.timeout_seconds * 1000) / 1000)),
            capabilities=payload.capabilities,
            enabled=payload.enabled,
            config_version=1,
        )
        db.add(profile)
        db.flush()
        # Persist the discoverable tenant/profile identity before touching the
        # non-transactional OS credential store.  Even if a later DB write
        # fails, account deletion can still locate and remove this key.
        _audit(db, user, "provider.created", profile, after=_public(profile))
        db.commit()
        db.refresh(profile)
        if payload.api_key:
            set_api_key(profile, payload.api_key)
            _audit(db, user, "provider.key.updated", profile, after={"has_api_key": True})
            db.commit()
            db.refresh(profile)
        return _mark_default(_public(profile), profile, user)
    except (ProviderError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/default")
def get_default_provider(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    provider_id = getattr(user, "default_provider_id", None)
    if not provider_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "provider_required", "message": "尚未设置默认模型 Provider"},
        )
    profile = _profile_query(db, provider_id, user)
    if profile is None or not profile.enabled:
        raise HTTPException(
            status_code=404,
            detail={"code": "provider_required", "message": "默认模型 Provider 不可用"},
        )
    return _mark_default(_public(profile), profile, user)


@router.put("/{provider_id}/default")
def put_default_provider(
    provider_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = _required_profile(db, provider_id, user)
    if not profile.enabled:
        raise HTTPException(status_code=409, detail="已停用的 Provider 不能设为默认")
    user.default_provider_id = profile.id
    _audit(db, user, "provider.default.updated", profile, after={"default": True})
    db.commit()
    return _mark_default(_public(profile), profile, user)


@router.get("/{provider_id}")
def get_provider(
    provider_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = _required_profile(db, provider_id, user)
    return _mark_default(_public(profile), profile, user)


@router.put("/{provider_id}")
@router.patch("/{provider_id}")
def update_provider(
    provider_id: str,
    payload: ProviderPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _required_profile(db, provider_id, user)
    old_protocol = profile.protocol
    previous_key: str | None = None
    key_attempted = False
    try:
        _apply_payload(profile, payload)
        _set_config_version(profile)
        if payload.api_key:
            previous_key = get_api_key(profile)
            key_attempted = True
            set_api_key(profile, payload.api_key)
        if old_protocol != profile.protocol and profile.protocol == "anthropic_messages" and not profile.api_version:
            profile.api_version = DEFAULT_ANTHROPIC_VERSION
        _audit(db, user, "provider.updated", profile, after=_public(profile))
        db.commit()
        db.refresh(profile)
        return _mark_default(_public(profile), profile, user)
    except (ProviderError, ValueError) as exc:
        db.rollback()
        if key_attempted:
            try:
                _restore_api_key(profile, previous_key)
            except ProviderError as restore_exc:
                raise HTTPException(
                    status_code=500,
                    detail="Provider 更新失败，且无法恢复原凭据",
                ) from restore_exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        if key_attempted:
            _restore_api_key(profile, previous_key)
        raise


@router.delete("/{provider_id}")
def remove_provider(
    provider_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _required_profile(db, provider_id, user)
    previous_key: str | None = None
    key_attempted = False
    try:
        previous_key = get_api_key(profile)
        key_attempted = True
        delete_api_key(profile)
        profile.enabled = False
        profile.deleted_at = datetime.now(UTC)
        if getattr(user, "default_provider_id", None) == profile.id:
            user.default_provider_id = None
        _audit(db, user, "provider.deleted", profile, after={"deleted": True})
        db.commit()
    except ProviderError as exc:
        db.rollback()
        if key_attempted:
            try:
                _restore_api_key(profile, previous_key)
            except ProviderError as restore_exc:
                raise HTTPException(
                    status_code=500,
                    detail="Provider 删除失败，且无法恢复原凭据",
                ) from restore_exc
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        if key_attempted:
            _restore_api_key(profile, previous_key)
        raise
    return {"id": profile.id, "deleted": True}


@router.post("/{provider_id}/key")
@router.put("/{provider_id}/key")
def save_provider_key(
    provider_id: str,
    payload: APIKeyPayload,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _required_profile(db, provider_id, user)
    previous_key: str | None = None
    key_attempted = False
    try:
        previous_key = get_api_key(profile)
        key_attempted = True
        set_api_key(profile, payload.api_key)
        _audit(db, user, "provider.key.updated", profile, after={"has_api_key": True})
        db.commit()
    except (ProviderError, ValueError) as exc:
        db.rollback()
        if key_attempted:
            try:
                _restore_api_key(profile, previous_key)
            except ProviderError as restore_exc:
                raise HTTPException(
                    status_code=500,
                    detail="凭据保存失败，且无法恢复原凭据",
                ) from restore_exc
        raise HTTPException(status_code=500, detail=f"无法保存到系统凭据库：{exc}") from exc
    except Exception:
        db.rollback()
        if key_attempted:
            _restore_api_key(profile, previous_key)
        raise
    return {"id": profile.id, "has_api_key": True}


@router.delete("/{provider_id}/key")
def remove_provider_key(
    provider_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = _required_profile(db, provider_id, user)
    previous_key: str | None = None
    key_attempted = False
    try:
        previous_key = get_api_key(profile)
        key_attempted = True
        delete_api_key(profile)
        _audit(db, user, "provider.key.deleted", profile, after={"has_api_key": False})
        db.commit()
    except ProviderError as exc:
        db.rollback()
        if key_attempted:
            try:
                _restore_api_key(profile, previous_key)
            except ProviderError as restore_exc:
                raise HTTPException(
                    status_code=500,
                    detail="凭据删除失败，且无法恢复原凭据",
                ) from restore_exc
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        if key_attempted:
            _restore_api_key(profile, previous_key)
        raise
    return {"id": profile.id, "has_api_key": False}


async def _test(profile: ProviderProfile) -> dict[str, Any]:
    adapter = provider_for(profile)
    try:
        return await adapter.test_connection()
    finally:
        await adapter.__aexit__(None, None, None)


@router.post("/test")
def test_provider_payload(
    payload: ProviderPayload,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Test an unsaved profile without writing it to the tenant database.

    This compatibility endpoint is useful for the settings form's "test
    connection" button.  A supplied key exists only on the transient in-memory
    profile for the duration of the request and is never persisted.
    """

    profile = ProviderProfile(
        id=new_id(),
        owner_id=_owner(user),
        name=payload.name,
        base_url=_base_url_for(payload.protocol, payload.base_url),
        protocol=payload.protocol,
        api_version=payload.api_version
        or (DEFAULT_ANTHROPIC_VERSION if payload.protocol == "anthropic_messages" else None),
        max_output_tokens=payload.max_output_tokens,
        anthropic_workspace_id=payload.anthropic_workspace_id,
        model_role_mapping=_role_mapping(payload),
        context_length=payload.context_length,
        timeout_seconds=max(1, int((payload.timeout_ms or payload.timeout_seconds * 1000) / 1000)),
        capabilities=payload.capabilities,
        enabled=payload.enabled,
        config_version=1,
    )
    try:
        validate_provider_url(profile.base_url)
        if payload.api_key:
            # Keep an unsaved test key in memory only.  Saved profiles use
            # set_api_key() above and the OS credential manager.
            profile._api_key_override = payload.api_key  # type: ignore[attr-defined]
        return asyncio.run(_test(profile))
    except ProviderError as exc:
        return {
            "ok": False,
            "status_code": exc.status_code,
            "retryable": exc.retryable,
            "message": str(exc),
        }


@router.post("/{provider_id}/test")
def test_provider(
    provider_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = _required_profile(db, provider_id, user)
    try:
        return asyncio.run(_test(profile))
    except ProviderError as exc:
        return {
            "ok": False,
            "status_code": exc.status_code,
            "retryable": exc.retryable,
            "message": str(exc),
        }


__all__ = ["ProviderPayload", "router"]
