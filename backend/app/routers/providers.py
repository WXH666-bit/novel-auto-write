"""Local model-provider profiles and credential-manager endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, ProviderProfile
from ..services.providers import (
    ProviderError,
    delete_api_key,
    get_api_key,
    provider_for,
    set_api_key,
)

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = "http://127.0.0.1:1234/v1"
    protocol: str = "chat_completions"
    model_role_mapping: dict[str, Any] = Field(default_factory=dict)
    context_length: int = Field(default=8192, ge=1)
    timeout_seconds: int = Field(default=120, ge=1)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    api_key: str | None = None
    # Friendly aliases used by the local React settings screen.
    default_model: str | None = None
    model_roles: dict[str, str] = Field(default_factory=dict)
    timeout_ms: int | None = Field(default=None, ge=1000)


def _public(profile: ProviderProfile) -> dict[str, Any]:
    roles = dict(profile.model_role_mapping or {})
    return {
        "id": profile.id,
        "name": profile.name,
        "base_url": profile.base_url,
        "protocol": profile.protocol,
        "model_role_mapping": roles,
        "model_roles": roles,
        "default_model": str(roles.get("default") or roles.get("writer") or ""),
        "context_length": profile.context_length,
        "timeout_seconds": profile.timeout_seconds,
        "timeout_ms": profile.timeout_seconds * 1000,
        "capabilities": profile.capabilities,
        "enabled": profile.enabled,
        "has_api_key": bool(get_api_key(profile)),
        "api_key_set": bool(get_api_key(profile)),
        "is_demo": profile.protocol == "demo",
    }


def _role_mapping(payload: ProviderPayload) -> dict[str, Any]:
    roles = dict(payload.model_role_mapping or {})
    roles.update(payload.model_roles or {})
    if payload.default_model:
        roles["default"] = payload.default_model
        roles.setdefault("writer", payload.default_model)
    return roles


def _default_profile(db: Session) -> ProviderProfile | None:
    return db.scalar(
        select(ProviderProfile)
        .where(ProviderProfile.enabled.is_(True))
        .order_by(ProviderProfile.created_at.asc())
    )


def _save_default(payload: ProviderPayload, db: Session) -> ProviderProfile:
    profile = _default_profile(db)
    if profile is None:
        profile = ProviderProfile(name=payload.name)
        db.add(profile)
        db.flush()
    profile.name = payload.name
    profile.base_url = payload.base_url
    profile.protocol = payload.protocol
    profile.model_role_mapping = _role_mapping(payload)
    profile.context_length = payload.context_length
    profile.timeout_seconds = max(
        1, int((payload.timeout_ms or payload.timeout_seconds * 1000) / 1000)
    )
    profile.capabilities = payload.capabilities
    profile.enabled = payload.enabled
    if payload.api_key:
        set_api_key(profile, payload.api_key)
    db.add(
        AuditLog(
            action="provider.default.updated",
            entity_type="provider_profile",
            entity_id=profile.id,
            after_json={
                "name": profile.name,
                "base_url": profile.base_url,
                "protocol": profile.protocol,
            },
        )
    )
    db.commit()
    db.refresh(profile)
    return profile


@router.get("")
def list_providers(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        _public(item)
        for item in db.scalars(select(ProviderProfile).order_by(ProviderProfile.created_at)).all()
    ]


@router.post("")
def create_provider(payload: ProviderPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = ProviderProfile(
        name=payload.name,
        base_url=payload.base_url,
        protocol=payload.protocol,
        model_role_mapping=_role_mapping(payload),
        context_length=payload.context_length,
        timeout_seconds=payload.timeout_seconds,
        capabilities=payload.capabilities,
        enabled=payload.enabled,
    )
    db.add(profile)
    db.flush()
    if payload.api_key:
        try:
            set_api_key(profile, payload.api_key)
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"无法保存到系统凭据库：{exc}") from exc
    db.add(
        AuditLog(
            action="provider.created",
            entity_type="provider_profile",
            entity_id=profile.id,
            after_json=_public(profile),
        )
    )
    db.commit()
    db.refresh(profile)
    return _public(profile)


@router.get("/default")
def get_default_provider(db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = _default_profile(db)
    if profile is None:
        raise HTTPException(status_code=404, detail="尚未配置模型；当前将使用 Demo Provider")
    return _public(profile)


@router.put("/default")
def put_default_provider(payload: ProviderPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return _public(_save_default(payload, db))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"模型配置保存失败：{exc}") from exc


@router.post("/test")
def test_default_provider(
    payload: ProviderPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        profile = _save_default(payload, db)
        return _run_provider_test(profile)
    except ProviderError as exc:
        return {
            "ok": False,
            "status_code": exc.status_code,
            "retryable": exc.retryable,
            "message": str(exc),
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"模型连接测试失败：{exc}") from exc


@router.get("/{provider_id}")
def get_provider(provider_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = db.get(ProviderProfile, provider_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return _public(profile)


@router.post("/{provider_id}/key")
def save_provider_key(
    provider_id: str, payload: dict[str, str], db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = db.get(ProviderProfile, provider_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    value = payload.get("api_key", "")
    if not value:
        raise HTTPException(status_code=422, detail="api_key 不能为空")
    try:
        set_api_key(profile, value)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法保存到系统凭据库：{exc}") from exc
    db.add(
        AuditLog(
            action="provider.key.updated",
            entity_type="provider_profile",
            entity_id=profile.id,
            after_json={"has_api_key": True},
        )
    )
    db.commit()
    return {"id": profile.id, "has_api_key": True}


@router.delete("/{provider_id}/key")
def remove_provider_key(provider_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = db.get(ProviderProfile, provider_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    delete_api_key(profile)
    db.add(
        AuditLog(
            action="provider.key.deleted",
            entity_type="provider_profile",
            entity_id=profile.id,
            after_json={"has_api_key": False},
        )
    )
    db.commit()
    return {"id": profile.id, "has_api_key": False}


@router.post("/{provider_id}/test")
def test_provider(provider_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = db.get(ProviderProfile, provider_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    try:
        return _run_provider_test(profile)
    except ProviderError as exc:
        return {
            "ok": False,
            "status_code": exc.status_code,
            "retryable": exc.retryable,
            "message": str(exc),
        }


def _run_provider_test(profile: ProviderProfile) -> dict[str, Any]:
    import asyncio

    return asyncio.run(provider_for(profile).test_connection())
