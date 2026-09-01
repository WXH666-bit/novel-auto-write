"""Account registration, email verification, sessions, and recovery."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import AUTH_MODE, AUTH_RATE_LIMIT_WINDOW_SECONDS, CSRF_COOKIE_NAME
from ..db import get_db
from ..models import AuditLog, EmailToken, Project, ProviderProfile, User
from ..schemas import (
    AuthConfigRead,
    ChangePasswordRequest,
    DeleteAccountRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    UserRead,
    VerifyEmailRequest,
)
from ..security import (
    as_utc,
    clear_login_failures,
    clear_session_cookies,
    consume_rate_limit,
    get_current_user,
    get_optional_user,
    get_session_from_request,
    hash_password,
    hash_secret,
    new_secret,
    new_session,
    normalize_email,
    normalize_username,
    register_login_failure,
    revoke_all_sessions,
    revoke_session,
    set_csrf_cookie,
    set_session_cookies,
    token_is_usable,
    utc_now,
    validate_password,
    validate_preauth_csrf,
    verify_password,
)
from ..services import mailer
from ..services.search import purge_project_search
from ..services.storage import stage_storage_deletion

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

VERIFY_TOKEN_TTL = timedelta(hours=24)
RESET_TOKEN_TTL = timedelta(minutes=30)
GENERIC_RECOVERY_MESSAGE = "如果该邮箱已注册，我们会发送一封邮件；请检查收件箱。"
AUTH_MODE_UNAVAILABLE_MESSAGE = "当前部署未启用邮箱认证功能"


class _AuthResponse(dict[str, Any]):
    """Typing-only marker for the JSON response helpers below."""


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "unknown")[:64]


def _token(
    db: Session, user: User, purpose: str, ttl: timedelta
) -> tuple[EmailToken, str]:
    raw = new_secret()
    item = EmailToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=hash_secret(raw),
        expires_at=utc_now() + ttl,
    )
    db.add(item)
    db.flush()
    return item, raw


def _find_user(db: Session, email: str) -> User | None:
    try:
        normalized = normalize_email(email)
    except ValueError:
        return None
    return db.scalar(select(User).where(User.email_normalized == normalized))


def _payload_identifier(payload: Any) -> str | None:
    """Return the mode-independent identifier accepted by auth schemas."""

    for value in (
        getattr(payload, "identifier", None),
        getattr(payload, "username", None),
        getattr(payload, "email", None),
    ):
        if value is not None and value.strip():
            return value
    return None


def _auth_mode_unavailable() -> None:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "auth_mode_unavailable", "message": AUTH_MODE_UNAVAILABLE_MESSAGE},
    )


def _require_email_auth_mode() -> None:
    if AUTH_MODE != "email":
        _auth_mode_unavailable()


def _public_user(user: User) -> UserRead:
    return UserRead.model_validate(user)


def _auth_payload(user: User, csrf_token: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"user": _public_user(user).model_dump(mode="json")}
    # Returning the CSRF value is convenient for non-browser clients.  It is
    # not a credential and remains protected by SameSite cookies/origin checks.
    if csrf_token:
        payload["csrf_token"] = csrf_token
    return payload


def _consume_token(db: Session, token: EmailToken) -> None:
    """Atomically reserve a one-time email token inside the request transaction."""

    used_at = utc_now()
    result = db.execute(
        update(EmailToken)
        .where(EmailToken.id == token.id, EmailToken.used_at.is_(None))
        .values(used_at=used_at)
    )
    if result.rowcount != 1:
        raise HTTPException(
            status_code=400,
            detail={"code": "token_used", "message": "链接已使用"},
        )
    token.used_at = used_at


def _invalidate_other_tokens(
    db: Session, user_id: str, purpose: str, current: EmailToken
) -> None:
    """Invalidate older outstanding links after a successful one-time action."""

    now = utc_now()
    outstanding = db.scalars(
        select(EmailToken).where(
            EmailToken.user_id == user_id,
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
            EmailToken.id != current.id,
        )
    ).all()
    for item in outstanding:
        item.used_at = now


def _audit(
    db: Session,
    *,
    action: str,
    user: User | None = None,
    entity_type: str | None = "user",
    entity_id: str | None = None,
    reason: str | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=user.id if user else None,
            actor="user" if user else "system",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id or (user.id if user else None),
            reason=reason,
            after_json=after,
        )
    )


def _verify_origin_for_auth_write(request: Request) -> None:
    # Auth writes happen before a session exists, so the normal dependency
    # cannot run CSRF.  Origin validation still protects browser requests.
    validate_preauth_csrf(request)


@router.get("/csrf")
def csrf_token(request: Request, response: Response) -> dict[str, str]:
    """Issue the readable half of the pre-auth double-submit token."""

    raw = request.cookies.get(CSRF_COOKIE_NAME) or new_secret()
    set_csrf_cookie(response, raw)
    return {"csrf_token": raw}


@router.get("/config", response_model=AuthConfigRead)
def auth_config() -> AuthConfigRead:
    """Expose the deployment-selected authentication capabilities publicly."""

    return AuthConfigRead(
        mode=AUTH_MODE,
        verification_required=AUTH_MODE == "email",
        password_reset_available=AUTH_MODE == "email",
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    _verify_origin_for_auth_write(request)
    ip = _client_ip(request)
    if not consume_rate_limit(
        db,
        "register",
        ip,
        limit=10,
        window_seconds=AUTH_RATE_LIMIT_WINDOW_SECONDS,
    ):
        db.commit()
        raise HTTPException(status_code=429, detail="注册请求过于频繁，请稍后再试")
    raw_identifier = _payload_identifier(payload)
    try:
        if raw_identifier is None:  # Pydantic normally catches this first.
            raise ValueError("必须提供登录标识")
        validate_password(payload.password)
        if AUTH_MODE == "username":
            username = normalize_username(raw_identifier)
            if db.scalar(select(User).where(User.username_normalized == username)) is not None:
                # Keep the rate-limit increment even when the account already exists.
                db.commit()
                raise HTTPException(status_code=409, detail="该用户名已注册")
            email = None
            email_normalized = None
        else:
            email = normalize_email(raw_identifier)
            username = None
            email_normalized = email
            if db.scalar(select(User).where(User.email_normalized == email)) is not None:
                # Keep the rate-limit increment even when the account already exists.
                db.commit()
                raise HTTPException(status_code=409, detail="该邮箱已注册")
    except HTTPException:
        raise
    except ValueError as exc:
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user = User(
        email=raw_identifier.strip() if AUTH_MODE == "email" else None,
        email_normalized=email_normalized,
        username=raw_identifier.strip() if AUTH_MODE == "username" else None,
        username_normalized=username,
        display_name=payload.display_name.strip() if payload.display_name else None,
        password_hash=hash_password(payload.password),
        # Username accounts have no email verification step and are usable
        # immediately; this flag remains true for compatibility with clients
        # that display the existing UserRead field.
        is_email_verified=AUTH_MODE == "username",
        is_active=True,
    )
    try:
        # The pre-check gives a friendly fast path; the unique index remains
        # the authority for simultaneous registrations of the same address.
        with db.begin_nested():
            db.add(user)
            db.flush()
    except IntegrityError as exc:
        db.commit()  # preserve the database-backed rate-limit increment
        detail = "该用户名已注册" if AUTH_MODE == "username" else "该邮箱已注册"
        raise HTTPException(status_code=409, detail=detail) from exc
    if AUTH_MODE == "email":
        _, raw_token = _token(db, user, "verify_email", VERIFY_TOKEN_TTL)
    else:
        raw_token = None
    _audit(
        db,
        action="auth.registered",
        user=user,
        after={"email": user.email, "username": user.username},
    )
    db.commit()
    if raw_token is not None:
        try:
            mailer.send_verification_email(user, raw_token)
        except mailer.MailDeliveryError:
            # Registration is still valid; the user can use resend-verification.
            logger.exception("verification email delivery failed")
    return {
        "user": _public_user(user).model_dump(mode="json"),
        "verification_required": AUTH_MODE == "email",
        "message": "注册成功，请验证邮箱后登录。" if AUTH_MODE == "email" else "注册成功，请直接登录。",
    }


def _verify_token(db: Session, raw_token: str, purpose: str) -> EmailToken:
    item = db.scalar(
        select(EmailToken)
        .where(EmailToken.token_hash == hash_secret(raw_token))
        .with_for_update()
    )
    if item is None or item.purpose != purpose:
        raise HTTPException(
            status_code=400,
            detail={"code": "token_invalid", "message": "链接无效或已过期"},
        )
    if item.used_at is not None:
        raise HTTPException(
            status_code=400,
            detail={"code": "token_used", "message": "链接已使用"},
        )
    if not token_is_usable(item, purpose):
        raise HTTPException(
            status_code=400,
            detail={"code": "token_expired", "message": "链接无效或已过期"},
        )
    return item


@router.post("/verify-email")
def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_email_auth_mode()
    _verify_origin_for_auth_write(request)
    item = _verify_token(db, payload.token, "verify_email")
    user = db.get(User, item.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail={"code": "token_invalid", "message": "链接无效或已过期"})
    _consume_token(db, item)
    _invalidate_other_tokens(db, user.id, "verify_email", item)
    user.is_email_verified = True
    _audit(db, action="auth.email_verified", user=user)
    _, raw_session, raw_csrf = new_session(
        db, user, user_agent=request.headers.get("user-agent"), ip_address=_client_ip(request)
    )
    db.commit()
    set_session_cookies(response, raw_session, raw_csrf)
    return {**_auth_payload(user, raw_csrf), "message": "邮箱验证成功。"}


@router.post("/resend-verification")
def resend_verification(
    payload: ResendVerificationRequest, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    _require_email_auth_mode()
    _verify_origin_for_auth_write(request)
    try:
        raw_identifier = _payload_identifier(payload)
        if raw_identifier is None:
            raise ValueError
        email = normalize_email(raw_identifier)
    except ValueError:
        # Keep the same response for malformed/unknown identifiers.
        return {"ok": True, "message": GENERIC_RECOVERY_MESSAGE}
    if not consume_rate_limit(
        db,
        "resend_verification",
        f"{_client_ip(request)}:{email}",
        limit=3,
        window_seconds=AUTH_RATE_LIMIT_WINDOW_SECONDS,
    ):
        db.commit()
        return {"ok": True, "message": GENERIC_RECOVERY_MESSAGE}
    user = db.scalar(
        select(User).where(
            User.email_normalized == email,
            User.is_active.is_(True),
            User.is_email_verified.is_(False),
        )
    )
    if user is not None:
        _, raw_token = _token(db, user, "verify_email", VERIFY_TOKEN_TTL)
        db.commit()
        try:
            mailer.send_verification_email(user, raw_token)
        except mailer.MailDeliveryError:
            logger.exception("verification email delivery failed")
    else:
        # Persist the limiter for unknown addresses too; the response remains
        # deliberately indistinguishable from the successful path.
        db.commit()
    return {"ok": True, "message": GENERIC_RECOVERY_MESSAGE}


@router.post("/login")
def login(
    payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    _verify_origin_for_auth_write(request)
    raw_identifier = _payload_identifier(payload)
    try:
        if raw_identifier is None:
            raise ValueError
        identifier = (
            normalize_username(raw_identifier)
            if AUTH_MODE == "username"
            else normalize_email(raw_identifier)
        )
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_credentials",
                "message": "用户名或密码错误" if AUTH_MODE == "username" else "邮箱或密码错误",
            },
        ) from None
    if not consume_rate_limit(
        db,
        "login",
        f"{_client_ip(request)}:{identifier}",
        limit=12,
        window_seconds=AUTH_RATE_LIMIT_WINDOW_SECONDS,
    ):
        db.commit()
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    identity_column = (
        User.username_normalized if AUTH_MODE == "username" else User.email_normalized
    )
    user = db.scalar(select(User).where(identity_column == identifier).with_for_update())
    locked_until = as_utc(user.locked_until) if user else None
    if locked_until and locked_until > utc_now():
        db.commit()
        raise HTTPException(status_code=429, detail={"code": "account_locked", "message": "登录失败次数过多，请稍后再试"})
    if user is None or not verify_password(user.password_hash, payload.password):
        register_login_failure(db, user)
        db.commit()
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_credentials",
                "message": "用户名或密码错误" if AUTH_MODE == "username" else "邮箱或密码错误",
            },
        )
    if not user.is_active:
        db.commit()
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_credentials",
                "message": "用户名或密码错误" if AUTH_MODE == "username" else "邮箱或密码错误",
            },
        )
    if AUTH_MODE == "email" and not user.is_email_verified:
        db.commit()
        raise HTTPException(status_code=403, detail={"code": "email_not_verified", "message": "请先验证邮箱"})
    clear_login_failures(user)
    _, raw_session, raw_csrf = new_session(
        db, user, user_agent=request.headers.get("user-agent"), ip_address=_client_ip(request)
    )
    _audit(db, action="auth.logged_in", user=user)
    db.commit()
    set_session_cookies(response, raw_session, raw_csrf)
    return _auth_payload(user, raw_csrf)


@router.post("/logout")
def logout(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    user = get_optional_user(request, db)
    if user is not None:
        session = get_session_from_request(request, db)
        if session is not None:
            # Optional logout is idempotent but a present session still needs
            # the same CSRF protection as every other state change.
            from ..security import validate_csrf

            validate_csrf(request, db, session)
            revoke_session(session)
        _audit(db, action="auth.logged_out", user=user)
        db.commit()
    clear_session_cookies(response)
    return {"ok": True}


@router.post("/logout-all")
def logout_all(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # get_current_user has already checked CSRF.  "All" includes this browser
    # session, so the caller is returned to the login screen as expected.
    revoked = revoke_all_sessions(db, user.id)
    _audit(db, action="auth.logged_out_all", user=user, after={"revoked": revoked})
    db.commit()
    clear_session_cookies(response)
    return {"ok": True, "revoked": revoked}


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)) -> UserRead:
    return _public_user(user)


@router.post("/forgot-password")
def forgot_password(
    payload: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    _require_email_auth_mode()
    _verify_origin_for_auth_write(request)
    try:
        raw_identifier = _payload_identifier(payload)
        if raw_identifier is None:
            raise ValueError
        email = normalize_email(raw_identifier)
    except ValueError:
        return {"ok": True, "message": GENERIC_RECOVERY_MESSAGE}
    if not consume_rate_limit(
        db,
        "forgot_password",
        f"{_client_ip(request)}:{email}",
        limit=3,
        window_seconds=AUTH_RATE_LIMIT_WINDOW_SECONDS,
    ):
        db.commit()
        return {"ok": True, "message": GENERIC_RECOVERY_MESSAGE}
    user = db.scalar(
        select(User).where(
            User.email_normalized == email,
            User.is_active.is_(True),
            User.is_email_verified.is_(True),
        )
    )
    if user is not None:
        _, raw_token = _token(db, user, "reset_password", RESET_TOKEN_TTL)
        db.commit()
        try:
            mailer.send_password_reset_email(user, raw_token)
        except mailer.MailDeliveryError:
            logger.exception("password reset email delivery failed")
    else:
        db.commit()
    return {"ok": True, "message": GENERIC_RECOVERY_MESSAGE}


@router.post("/reset-password")
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_email_auth_mode()
    _verify_origin_for_auth_write(request)
    try:
        validate_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = _verify_token(db, payload.token, "reset_password")
    user = db.get(User, item.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail={"code": "token_invalid", "message": "链接无效或已过期"})
    user.password_hash = hash_password(payload.new_password)
    clear_login_failures(user)
    _consume_token(db, item)
    _invalidate_other_tokens(db, user.id, "reset_password", item)
    revoke_all_sessions(db, user.id)
    _audit(db, action="auth.password_reset", user=user)
    db.commit()
    clear_session_cookies(response)
    return {"ok": True, "message": "密码已重置，请重新登录。"}


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(status_code=400, detail={"code": "invalid_password", "message": "当前密码错误"})
    try:
        validate_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user.password_hash = hash_password(payload.new_password)
    clear_login_failures(user)
    current = get_session_from_request(request, db)
    revoked = 0
    if payload.revoke_other_sessions:
        revoked = revoke_all_sessions(db, user.id, current.id if current else None)
    _audit(db, action="auth.password_changed", user=user, after={"revoked_sessions": revoked})
    db.commit()
    return {"ok": True, "revoked": revoked, "user": _public_user(user).model_dump(mode="json")}


def _delete_user_credentials(db: Session, user: User) -> Any:
    """Delete all OS credential-store entries before account deletion.

    The provider adapter may expose a stronger bulk implementation.  The
    fallback supports the original keyring integration and is intentionally
    fail-closed: an unexpected credential-store error aborts account removal.
    """

    profiles = db.scalars(select(ProviderProfile).where(ProviderProfile.owner_id == user.id)).all()
    try:
        from ..services import providers as provider_service

        bulk_delete = getattr(provider_service, "delete_user_credentials", None)
        if callable(bulk_delete):
            # Current provider service accepts (session, user_id); retain a
            # small compatibility path for adapters from the first release.
            import inspect

            parameters = list(inspect.signature(bulk_delete).parameters)
            if parameters and parameters[0] in {"session", "db", "database"}:
                return bulk_delete(db, user.id)
            else:
                return bulk_delete(user.id, profiles)
        delete_api_key = getattr(provider_service, "delete_api_key", None)
        if callable(delete_api_key):
            for profile in profiles:
                delete_api_key(profile)
            return None
    except ImportError:
        pass
    import keyring

    for profile in profiles:
        for username in (f"{user.id}:{profile.id}", profile.id):
            try:
                keyring.delete_password("novel-auto-write", username)
            except Exception as exc:
                # Backends report a missing secret differently; only tolerate
                # the explicit not-found family, fail on all other errors.
                name = exc.__class__.__name__.lower()
                message = str(exc).lower()
                missing = (
                    "notfound" in name
                    or "not found" in message
                    or "no password" in message
                    or "does not exist" in message
                )
                if not missing:
                    raise RuntimeError("无法删除系统凭据，账号删除已中止") from exc


@router.delete("/account")
def delete_account(
    payload: DeleteAccountRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if not verify_password(user.password_hash, payload.password):
        raise HTTPException(status_code=400, detail={"code": "invalid_password", "message": "密码错误"})
    # Credential deletion is intentionally first and fail-closed.
    credential_deletion: Any = None
    try:
        credential_deletion = _delete_user_credentials(db, user)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "code": "credential_delete_failed",
                "message": "系统凭据删除失败，账号未注销",
            },
        ) from exc
    quarantine = None
    try:
        projects = db.scalars(select(Project).where(Project.owner_id == user.id)).all()
        project_ids = [project.id for project in projects]
        quarantine = stage_storage_deletion(owner_id=user.id)
        for project in projects:
            purge_project_search(db, owner_id=user.id, project_id=project.id)
            db.delete(project)
        for profile in db.scalars(select(ProviderProfile).where(ProviderProfile.owner_id == user.id)).all():
            db.delete(profile)
        _audit(
            db,
            action="auth.account_deleted",
            user=None,
            entity_type="user",
            entity_id=user.id,
            after={"project_ids": project_ids},
        )
        db.delete(user)
        db.commit()
    except Exception:
        db.rollback()
        if quarantine is not None:
            quarantine.restore()
        restore_credentials = getattr(credential_deletion, "restore", None)
        if callable(restore_credentials):
            restore_credentials()
        raise
    if quarantine is not None:
        quarantine.finalize()
    finalize_credentials = getattr(credential_deletion, "finalize", None)
    if callable(finalize_credentials):
        finalize_credentials()
    clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


__all__ = ["router"]
