"""Regression tests for account lifecycle and server-side session security."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app import db as db_module
from backend.app import models
from backend.app import security as security_module
from backend.app.db import create_engine_for_url, get_db, init_db
from backend.app.main import app
from backend.app.routers import auth as auth_router
from backend.app.schemas import DeleteAccountRequest
from backend.app.security import hash_password, hash_secret, utc_now
from backend.app.services import mailer
from backend.app.services import providers as provider_service
from backend.app.services.generation import create_generation_run


@pytest.fixture
def auth_client(tmp_path, monkeypatch) -> Iterator[tuple[TestClient, dict[str, str], Any]]:
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'auth.sqlite3').as_posix()}")
    init_db(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", testing_session)
    # The app lifespan uses the module-level initializer; keep it on this
    # isolated test engine rather than the user's real data directory.
    monkeypatch.setattr(db_module, "init_db", lambda *_args, **_kwargs: init_db(engine))

    def override_db() -> Iterator[Session]:
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    tokens: dict[str, str] = {}
    monkeypatch.setattr(
        mailer,
        "send_verification_email",
        lambda _user, token: tokens.__setitem__("verify", token),
    )
    monkeypatch.setattr(
        mailer,
        "send_password_reset_email",
        lambda _user, token: tokens.__setitem__("reset", token),
    )
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            yield client, tokens, testing_session
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_register_verify_login_and_csrf(auth_client) -> None:
    client, tokens, _factory = auth_client
    response = client.post(
        "/api/auth/register",
        json={
            "email": "Writer@Example.com",
            "password": "a sufficiently long password",
            "display_name": "作者",
        },
    )
    assert response.status_code == 201
    assert response.json()["verification_required"] is True
    assert client.get("/api/auth/me").status_code == 401

    response = client.post("/api/auth/verify-email", json={"token": tokens["verify"]})
    assert response.status_code == 200
    assert response.json()["user"]["is_email_verified"] is True
    cookies = response.headers.get("set-cookie", "").lower()
    assert "novel_session=" in cookies
    assert "httponly" in cookies
    assert "samesite=lax" in cookies
    assert client.get("/api/auth/me").status_code == 200

    # A verification token is single-use, and authenticated unsafe requests
    # need the readable CSRF cookie echoed in the request header.
    assert client.post("/api/auth/verify-email", json={"token": tokens["verify"]}).status_code == 400
    assert client.post("/api/auth/logout").status_code == 403
    csrf = client.cookies.get("novel_csrf")
    assert csrf
    forbidden_origin = client.post(
        "/api/projects",
        json={"title": "恶意来源"},
        headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
    )
    assert forbidden_origin.status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_email_action_tokens_use_url_fragments_not_query_strings(monkeypatch) -> None:
    bodies: list[str] = []
    monkeypatch.setattr(
        mailer,
        "send_email",
        lambda _to, _subject, body, _html=None: bodies.append(body),
    )
    user = models.User(
        email="links@example.test",
        email_normalized="links@example.test",
        password_hash="test",
    )
    mailer.send_verification_email(user, "verify-secret")
    mailer.send_password_reset_email(user, "reset-secret")
    assert "verify-email#token=verify-secret" in bodies[0]
    assert "reset-password#token=reset-secret" in bodies[1]
    assert "?token=" not in "".join(bodies)


def test_production_preauth_writes_require_double_submit(auth_client, monkeypatch) -> None:
    client, _tokens, _factory = auth_client
    monkeypatch.setattr(security_module, "CSRF_ENFORCE", True)
    payload = {
        "email": "preauth@example.test",
        "password": "a sufficiently long password",
    }
    first = client.post(
        "/api/auth/register",
        json=payload,
        headers={"Origin": "http://127.0.0.1"},
    )
    assert first.status_code == 403
    assert first.json()["detail"]["code"] == "csrf_invalid"
    token = client.cookies.get("novel_csrf")
    assert token
    retry = client.post(
        "/api/auth/register",
        json=payload,
        headers={
            "Origin": "http://127.0.0.1",
            "X-CSRF-Token": token,
        },
    )
    assert retry.status_code == 201


def test_password_recovery_is_uniform_and_revokes_sessions(auth_client) -> None:
    client, tokens, _factory = auth_client
    client.post(
        "/api/auth/register",
        json={"email": "recovery@example.com", "password": "a sufficiently long password"},
    )
    client.post("/api/auth/verify-email", json={"token": tokens["verify"]})
    client.post("/api/auth/logout", headers={"X-CSRF-Token": client.cookies["novel_csrf"]})
    client.post("/api/auth/forgot-password", json={"email": "recovery@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "unknown@example.com"})
    assert unknown.status_code == 200
    assert unknown.json()["message"] == "如果该邮箱已注册，我们会发送一封邮件；请检查收件箱。"
    reset = client.post(
        "/api/auth/reset-password",
        json={"token": tokens["reset"], "new_password": "an even longer replacement password"},
    )
    assert reset.status_code == 200
    assert client.post("/api/auth/reset-password", json={"token": tokens["reset"], "new_password": "another password here"}).status_code == 400
    login = client.post(
        "/api/auth/login",
        json={"email": "RECOVERY@example.com", "password": "an even longer replacement password"},
    )
    assert login.status_code == 200


def test_logout_all_includes_current_session(auth_client) -> None:
    client, tokens, _factory = auth_client
    client.post(
        "/api/auth/register",
        json={"email": "all@example.com", "password": "a sufficiently long password"},
    )
    client.post("/api/auth/verify-email", json={"token": tokens["verify"]})
    csrf = client.cookies["novel_csrf"]
    result = client.post("/api/auth/logout-all", headers={"X-CSRF-Token": csrf})
    assert result.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_expired_token_and_login_lockout(auth_client) -> None:
    client, tokens, factory = auth_client
    client.post(
        "/api/auth/register",
        json={"email": "locked@example.com", "password": "a sufficiently long password"},
    )
    expired = tokens["verify"]
    with factory() as db:
        token = db.scalar(
            select(models.EmailToken).where(
                models.EmailToken.token_hash == hash_secret(expired)
            )
        )
        assert token is not None
        token.expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    response = client.post("/api/auth/verify-email", json={"token": expired})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "token_expired"

    client.post(
        "/api/auth/register",
        json={"email": "active@example.com", "password": "another sufficiently long password"},
    )
    client.post("/api/auth/verify-email", json={"token": tokens["verify"]})
    csrf = client.cookies["novel_csrf"]
    client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    for _ in range(8):
        wrong = client.post(
            "/api/auth/login",
            json={"email": "active@example.com", "password": "definitely wrong"},
        )
        assert wrong.status_code == 401
    locked = client.post(
        "/api/auth/login",
        json={
            "email": "active@example.com",
            "password": "another sufficiently long password",
        },
    )
    assert locked.status_code == 429
    assert locked.json()["detail"]["code"] == "account_locked"


def test_change_password_revokes_other_device_only(auth_client) -> None:
    client, tokens, _factory = auth_client
    old_password = "a sufficiently long password"
    client.post(
        "/api/auth/register",
        json={"email": "devices@example.com", "password": old_password},
    )
    client.post("/api/auth/verify-email", json={"token": tokens["verify"]})
    other = TestClient(app, base_url="http://127.0.0.1")
    with other:
        login = other.post(
            "/api/auth/login",
            json={"email": "devices@example.com", "password": old_password},
        )
        assert login.status_code == 200
        changed = client.post(
            "/api/auth/change-password",
            json={
                "current_password": old_password,
                "new_password": "a replacement password long enough",
                "revoke_other_sessions": True,
            },
            headers={"X-CSRF-Token": client.cookies["novel_csrf"]},
        )
        assert changed.status_code == 200
        assert client.get("/api/auth/me").status_code == 200
        assert other.get("/api/auth/me").status_code == 401


class _FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.values[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class _FailingDeleteKeyring(_FakeKeyring):
    def delete_password(self, service: str, username: str) -> None:
        raise RuntimeError("credential manager unavailable")


def test_account_deletion_removes_private_credentials_and_rows(auth_client, monkeypatch) -> None:
    client, tokens, factory = auth_client
    fake_keyring = _FakeKeyring()
    monkeypatch.setattr(provider_service, "keyring", fake_keyring)
    password = "a sufficiently long password"
    client.post(
        "/api/auth/register",
        json={"email": "delete@example.com", "password": password},
    )
    client.post("/api/auth/verify-email", json={"token": tokens["verify"]})
    user_id = client.get("/api/auth/me").json()["id"]
    csrf_header = {"X-CSRF-Token": client.cookies["novel_csrf"]}
    project = client.post(
        "/api/projects", json={"title": "待删除小说"}, headers=csrf_header
    )
    assert project.status_code == 201
    raw_key = "sk-private-never-in-project-zip"
    provider = client.post(
        "/api/providers",
        json={
            "name": "私有模型",
            "protocol": "chat_completions",
            "base_url": "http://127.0.0.1:9999/v1",
            "default_model": "private-model",
            "api_key": raw_key,
        },
        headers=csrf_header,
    )
    assert provider.status_code == 201, provider.text
    with factory() as db:
        user = db.get(models.User, user_id)
        stored_provider = db.get(models.ProviderProfile, provider.json()["id"])
        stored_project = db.get(models.Project, project.json()["id"])
        assert user is not None and stored_provider is not None and stored_project is not None
        user.default_provider_id = stored_provider.id
        db.commit()
        run = create_generation_run(
            db,
            stored_project,
            {"idempotency_key": "secret-snapshot-check"},
        ).run
        persisted_snapshot = str(
            {
                "input": run.input_snapshot,
                "model": run.model_params,
                "provider": run.provider_snapshot,
            }
        )
        assert raw_key not in persisted_snapshot
    exported = client.get(f"/api/projects/{project.json()['id']}/export")
    assert exported.status_code == 200
    assert raw_key.encode() not in exported.content
    assert fake_keyring.values

    deleted = client.request(
        "DELETE",
        "/api/auth/account",
        json={"password": password},
        headers=csrf_header,
    )
    assert deleted.status_code == 204, deleted.text
    assert not fake_keyring.values
    with factory() as db:
        assert db.get(models.User, user_id) is None
        assert db.scalar(
            select(models.Project).where(models.Project.id == project.json()["id"])
        ) is None


def test_account_deletion_aborts_when_credential_store_fails(auth_client, monkeypatch) -> None:
    client, tokens, factory = auth_client
    keyring = _FailingDeleteKeyring()
    monkeypatch.setattr(provider_service, "keyring", keyring)
    password = "a sufficiently long password"
    client.post(
        "/api/auth/register",
        json={"email": "keep@example.com", "password": password},
    )
    client.post("/api/auth/verify-email", json={"token": tokens["verify"]})
    user_id = client.get("/api/auth/me").json()["id"]
    csrf_header = {"X-CSRF-Token": client.cookies["novel_csrf"]}
    provider = client.post(
        "/api/providers",
        json={
            "name": "不能遗留的密钥",
            "protocol": "chat_completions",
            "base_url": "http://127.0.0.1:9999/v1",
            "default_model": "private-model",
            "api_key": "sk-delete-failure-test",
        },
        headers=csrf_header,
    )
    assert provider.status_code == 201

    rejected = client.request(
        "DELETE",
        "/api/auth/account",
        json={"password": password},
        headers=csrf_header,
    )
    assert rejected.status_code == 500
    assert rejected.json()["detail"]["code"] == "credential_delete_failed"
    with factory() as db:
        assert db.get(models.User, user_id) is not None
        assert db.get(models.ProviderProfile, provider.json()["id"]) is not None


def test_account_deletion_restores_credentials_when_storage_stage_fails(
    auth_client, monkeypatch
) -> None:
    client, tokens, factory = auth_client
    keyring = _FakeKeyring()
    monkeypatch.setattr(provider_service, "keyring", keyring)
    password = "a sufficiently long password"
    client.post(
        "/api/auth/register",
        json={"email": "restore-key@example.com", "password": password},
    )
    client.post("/api/auth/verify-email", json={"token": tokens["verify"]})
    user_id = client.get("/api/auth/me").json()["id"]
    csrf_header = {"X-CSRF-Token": client.cookies["novel_csrf"]}
    provider = client.post(
        "/api/providers",
        json={
            "name": "需要回滚的密钥",
            "protocol": "chat_completions",
            "base_url": "http://127.0.0.1:9999/v1",
            "default_model": "private-model",
            "api_key": "sk-restore-after-storage-failure",
        },
        headers=csrf_header,
    )
    assert provider.status_code == 201
    before = dict(keyring.values)
    monkeypatch.setattr(
        auth_router,
        "stage_storage_deletion",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("storage unavailable")),
    )

    with pytest.raises(RuntimeError, match="storage unavailable"):
        client.request(
            "DELETE",
            "/api/auth/account",
            json={"password": password},
            headers=csrf_header,
        )
    assert keyring.values == before
    with factory() as db:
        assert db.get(models.User, user_id) is not None
        assert db.get(models.ProviderProfile, provider.json()["id"]) is not None


def test_account_deletion_restores_credentials_when_database_commit_fails(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'delete-rollback.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    keyring = _FakeKeyring()
    monkeypatch.setattr(provider_service, "keyring", keyring)

    class _NoopQuarantine:
        def restore(self) -> None:
            return None

        def finalize(self) -> None:
            return None

    monkeypatch.setattr(
        auth_router,
        "stage_storage_deletion",
        lambda **_kwargs: _NoopQuarantine(),
    )
    password = "a sufficiently long password"
    with factory() as db:
        user = models.User(
            email="db-rollback@example.test",
            email_normalized="db-rollback@example.test",
            password_hash=hash_password(password),
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        profile = models.ProviderProfile(
            owner_id=user.id,
            name="rollback",
            base_url="http://127.0.0.1:9999/v1",
            protocol="chat_completions",
            model_role_mapping={"default": "private-model"},
        )
        db.add(profile)
        db.commit()
        provider_service.set_api_key(profile, "sk-restore-after-db-failure")
        db.commit()
        before = dict(keyring.values)
        user_id = user.id
        provider_id = profile.id

        monkeypatch.setattr(
            db,
            "commit",
            lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
        )
        with pytest.raises(RuntimeError, match="database unavailable"):
            auth_router.delete_account(
                DeleteAccountRequest(password=password),
                request=None,  # type: ignore[arg-type]
                response=Response(),
                user=user,
                db=db,
            )
        assert keyring.values == before

    with factory() as verification:
        assert verification.get(models.User, user_id) is not None
        assert verification.get(models.ProviderProfile, provider_id) is not None
    engine.dispose()
