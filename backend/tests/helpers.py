"""Shared authenticated tenant and deterministic model helpers for tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.config import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from backend.app.security import hash_secret, utc_now
from backend.app.services.providers import ProviderResponse


class FakeProvider:
    async def complete(
        self,
        _messages: list[dict[str, str]],
        *,
        role: str = "writer",
        **_kwargs: Any,
    ) -> ProviderResponse:
        content = (
            "林渡沿着雾港旧堤走向灯塔，发现门轴上留着新鲜的盐霜。他没有贸然揭开幕后人物，只把线索记进随身札记。"
            if role in {"writer", "drafter", "reviser"}
            else "本章推进灯塔线索，保留幕后人物悬念。"
        )
        return ProviderResponse(
            content=content,
            raw={"test": True},
            model=f"fake-{role}",
            usage={"input_tokens": 10, "output_tokens": 20},
            request_id="fake-request",
        )

    async def structured(
        self,
        _messages: list[dict[str, str]],
        _schema: dict[str, Any],
        *,
        role: str = "extractor",
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], ProviderResponse]:
        if role == "extractor":
            payload: dict[str, Any] = {
                "facts": [],
                "canon_changes": [],
                "issues": [],
                "summary": "线索被谨慎推进。",
            }
        else:
            payload = {"issues": [], "summary": "未发现阻断问题。"}
        response = ProviderResponse(
            content="{}",
            raw={"test": True},
            model=f"fake-{role}",
            usage={"input_tokens": 10, "output_tokens": 10},
            request_id="fake-request",
        )
        return payload, response

    async def test_connection(self) -> dict[str, Any]:
        return {"ok": True, "models": ["fake-model"]}

    async def __aexit__(self, *_args: Any) -> None:
        return None


def install_fake_provider(monkeypatch: Any) -> None:
    from backend.app.services import generation, providers, reviews

    monkeypatch.setattr(generation, "provider_for", lambda _profile: FakeProvider())
    monkeypatch.setattr(providers, "provider_for", lambda _profile: FakeProvider())
    monkeypatch.setattr(reviews, "provider_for", lambda _profile: FakeProvider())


def seed_tenant(
    db: Session,
    *,
    email: str = "writer@example.test",
    with_provider: bool = True,
) -> tuple[models.User, models.ProviderProfile | None]:
    user = models.User(
        email=email,
        email_normalized=email.casefold(),
        display_name="测试作者",
        password_hash="test-only",
        is_email_verified=True,
        is_active=True,
    )
    db.add(user)
    db.flush()
    profile: models.ProviderProfile | None = None
    if with_provider:
        profile = models.ProviderProfile(
            owner_id=user.id,
            name="测试模型",
            base_url="http://127.0.0.1:9999/v1",
            protocol="chat_completions",
            model_role_mapping={"default": "fake-model", "writer": "fake-model"},
            enabled=True,
        )
        db.add(profile)
        db.flush()
        user.default_provider_id = profile.id
    db.commit()
    return user, profile


def authenticate_client(
    client: TestClient,
    session_factory: Any,
    *,
    with_provider: bool = True,
    email: str = "writer@example.test",
) -> str:
    identity = email.casefold().replace("@", "-").replace(".", "-")
    raw_session = f"test-session-token-{identity}"
    raw_csrf = f"test-csrf-token-{identity}"
    with session_factory() as db:
        user, _ = seed_tenant(db, email=email, with_provider=with_provider)
        db.add(
            models.UserSession(
                user_id=user.id,
                token_hash=hash_secret(raw_session),
                csrf_token_hash=hash_secret(raw_csrf),
                expires_at=utc_now() + timedelta(days=365),
            )
        )
        db.commit()
        user_id = user.id
    client.cookies.set(SESSION_COOKIE_NAME, raw_session)
    client.cookies.set(CSRF_COOKIE_NAME, raw_csrf)
    client.headers.update(
        {
            "X-CSRF-Token": raw_csrf,
            "Origin": "http://127.0.0.1",
        }
    )
    return user_id


__all__ = [
    "FakeProvider",
    "authenticate_client",
    "install_fake_provider",
    "seed_tenant",
]
