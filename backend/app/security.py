"""Authentication primitives and tenant-safe FastAPI dependencies.

The API deliberately uses opaque server-side sessions.  A browser receives a
random session value and a separate random CSRF value; only SHA-256 digests
are stored.  No bearer token or password is ever placed in a project export,
generation snapshot, or log message.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import (
    AUTH_LOCKOUT_MINUTES,
    AUTH_MAX_LOGIN_ATTEMPTS,
    AUTH_MODE,
    AUTH_RATE_LIMIT_WINDOW_SECONDS,
    CORS_ORIGINS,
    CSRF_COOKIE_NAME,
    CSRF_ENFORCE,
    LEGACY_OWNER_ID,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
    SESSION_TTL_DAYS,
)
from .db import get_db
from .models import (
    AuthRateLimit,
    EmailToken,
    Project,
    ProviderProfile,
    User,
    UserSession,
    new_id,
)

try:  # Imported lazily in production installs so ``app`` can still show a useful error.
    from pwdlib import PasswordHash
except ImportError:  # pragma: no cover - exercised only before dependency installation
    PasswordHash = None  # type: ignore[assignment,misc]


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_USERNAME_PUNCTUATION = frozenset("._-")


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Treat SQLite's timezone-less return values as UTC."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_email(email: str) -> str:
    value = email.strip().casefold()
    if len(value) > 320 or not _EMAIL_RE.fullmatch(value):
        raise ValueError("请输入有效邮箱地址")
    return value


def normalize_username(username: str) -> str:
    """Normalize the deployment-local username used for identity lookups."""

    value = unicodedata.normalize("NFKC", username).strip().casefold()
    if (
        len(value) < 3
        or len(value) > 64
        or not value[0].isalnum()
        or not value[-1].isalnum()
        or any(
            not character.isalnum() and character not in _USERNAME_PUNCTUATION
            for character in value
        )
    ):
        raise ValueError("用户名需为 3 至 64 个字符，只能使用文字、数字及中间的 . _ -")
    return value


def validate_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("密码至少需要 12 个字符")
    if len(password) > 128:
        raise ValueError("密码不能超过 128 个字符")
    return password


def _password_hasher() -> Any:
    if PasswordHash is None:
        raise RuntimeError("未安装 pwdlib[argon2]；请先安装后端依赖")
    # pwdlib's recommended hasher is Argon2id when the argon2 extra is present.
    return PasswordHash.recommended()


def hash_password(password: str) -> str:
    validate_password(password)
    return str(_password_hasher().hash(password))


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return bool(_password_hasher().verify(password, password_hash))
    except Exception:
        # Malformed/legacy hashes must behave like a wrong password, not leak
        # implementation details or abort login.
        return False


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compare_secret(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def auth_error(code: str, message: str, http_status: int = status.HTTP_401_UNAUTHORIZED) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, "message": message})


def _session_from_request(request: Request, db: Session) -> UserSession | None:
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        return None
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_secret(raw)))
    if session is None or session.revoked_at is not None:
        return None
    expires = as_utc(session.expires_at)
    if expires is None or expires <= utc_now():
        return None
    return session


def validate_origin(request: Request) -> None:
    """Validate browser Origin for state-changing API requests.

    A missing Origin is tolerated for local command-line clients.  Production
    can require it by setting ``NOVEL_CSRF_ENFORCE=1`` (the default there).
    """

    origin = request.headers.get("origin")
    if not origin:
        if CSRF_ENFORCE:
            raise auth_error("origin_required", "缺少 Origin", status.HTTP_403_FORBIDDEN)
        return
    if origin not in set(CORS_ORIGINS):
        # Same-origin deployments may not list the public origin in CORS when
        # serving the bundled frontend; compare it to the request origin.
        request_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origin != request_origin:
            raise auth_error("origin_forbidden", "请求来源不受信任", status.HTTP_403_FORBIDDEN)


def validate_csrf(request: Request, db: Session, session: UserSession | None = None) -> None:
    """Validate double-submit CSRF for an authenticated unsafe request."""

    if request.method.upper() in SAFE_METHODS:
        return
    validate_origin(request)
    # Authentication endpoints are intentionally usable before a session is
    # created.  Their Origin is still checked above in production.
    if session is None:
        session = _session_from_request(request, db)
    if session is None:
        return
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME, "")
    header_value = request.headers.get("x-csrf-token", "")
    if not cookie_value or not header_value or not compare_secret(cookie_value, header_value):
        raise auth_error("csrf_invalid", "CSRF 校验失败", status.HTTP_403_FORBIDDEN)
    if not compare_secret(hash_secret(header_value), session.csrf_token_hash):
        raise auth_error("csrf_invalid", "CSRF 校验失败", status.HTTP_403_FORBIDDEN)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Return the authenticated, verified user or raise a generic 401/403.

    This function is intentionally a normal dependency (rather than a class)
    so routers can use ``Depends(get_current_user)`` directly.  It also runs
    CSRF protection for unsafe requests, so every protected write gets the
    same check even if a route forgets a second dependency.
    """

    session = _session_from_request(request, db)
    if session is None:
        raise auth_error("not_authenticated", "请先登录")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active or user.id == LEGACY_OWNER_ID:
        raise auth_error("not_authenticated", "请先登录")
    if AUTH_MODE == "email":
        if not user.email_normalized:
            raise auth_error("not_authenticated", "请先登录")
        if not user.is_email_verified:
            raise auth_error("email_not_verified", "请先验证邮箱", status.HTTP_403_FORBIDDEN)
    elif not user.username_normalized:
        # A session created under another deployment mode must not become a
        # login bypass after the operator switches identity modes.
        raise auth_error("not_authenticated", "请先登录")
    session.last_seen_at = utc_now()
    validate_csrf(request, db, session)
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    session = _session_from_request(request, db)
    if session is None:
        return None
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    if AUTH_MODE == "email":
        if not user.email_normalized or not user.is_email_verified:
            return None
    elif not user.username_normalized:
        return None
    return user


def user_id_of(user: User | str) -> str:
    return user.id if isinstance(user, User) else str(user)


def require_owned_project(db: Session, project_id: str, user_id: User | str) -> Project:
    """Load only a project's owner-visible row; cross-tenant IDs are 404."""

    owner_id = user_id_of(user_id)
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def require_owned_provider(db: Session, provider_id: str, user_id: User | str) -> ProviderProfile:
    owner_id = user_id_of(user_id)
    profile = db.scalar(
        select(ProviderProfile).where(
            ProviderProfile.id == provider_id,
            ProviderProfile.owner_id == owner_id,
            ProviderProfile.deleted_at.is_(None),
        )
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return profile


def new_session(
    db: Session,
    user: User,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[UserSession, str, str]:
    raw_session = new_secret()
    raw_csrf = new_secret()
    session = UserSession(
        id=new_id(),
        user_id=user.id,
        token_hash=hash_secret(raw_session),
        csrf_token_hash=hash_secret(raw_csrf),
        user_agent=(user_agent or "")[:500] or None,
        ip_address=(ip_address or "")[:64] or None,
        expires_at=utc_now() + timedelta(days=max(1, SESSION_TTL_DAYS)),
        last_seen_at=utc_now(),
    )
    db.add(session)
    return session, raw_session, raw_csrf


def set_session_cookies(response: Response, raw_session: str, raw_csrf: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_session,
        max_age=max(1, SESSION_TTL_DAYS) * 86400,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    set_csrf_cookie(response, raw_csrf)


def set_csrf_cookie(response: Response, raw_csrf: str) -> None:
    response.set_cookie(
        CSRF_COOKIE_NAME,
        raw_csrf,
        max_age=max(1, SESSION_TTL_DAYS) * 86400,
        httponly=False,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    # Keep the readable double-submit token so the signed-out browser can use
    # register/forgot-password without a reload.  A successful login rotates
    # it and binds the new value to the new server-side session.


def validate_preauth_csrf(request: Request) -> None:
    """Protect session-creating auth writes in hardened production mode."""

    validate_origin(request)
    if not CSRF_ENFORCE:
        return
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME, "")
    header_value = request.headers.get("x-csrf-token", "")
    if not cookie_value or not header_value or not compare_secret(cookie_value, header_value):
        raise auth_error("csrf_invalid", "CSRF 校验失败", status.HTTP_403_FORBIDDEN)


def revoke_session(session: UserSession) -> None:
    session.revoked_at = utc_now()


def revoke_all_sessions(db: Session, user_id: str, except_session_id: str | None = None) -> int:
    sessions = db.scalars(
        select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
    ).all()
    count = 0
    for session in sessions:
        if except_session_id and session.id == except_session_id:
            continue
        revoke_session(session)
        count += 1
    return count


def consume_rate_limit(
    db: Session,
    action: str,
    key: str,
    *,
    limit: int,
    window_seconds: int = AUTH_RATE_LIMIT_WINDOW_SECONDS,
    block_seconds: int = 0,
) -> bool:
    """Increment a DB-backed counter and return whether the request is allowed."""

    now = utc_now()
    key_hash = hash_secret(f"{action}:{key.casefold().strip()}")
    statement = (
        select(AuthRateLimit)
        .where(
            AuthRateLimit.action == action,
            AuthRateLimit.key_hash == key_hash,
        )
        .with_for_update()
    )
    row = db.scalar(statement)
    if row is None:
        candidate = AuthRateLimit(
            action=action,
            key_hash=key_hash,
            window_started_at=now,
            count=0,
        )
        try:
            # The savepoint keeps the request transaction usable when two
            # workers create the same limiter row at the same instant.
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            row = candidate
        except IntegrityError:
            row = db.scalar(statement)
            if row is None:  # pragma: no cover - defensive database failure
                raise
    blocked_until = as_utc(row.blocked_until)
    if blocked_until and blocked_until > now:
        return False
    started = as_utc(row.window_started_at) or now
    if (now - started).total_seconds() >= window_seconds:
        row.window_started_at = now
        row.count = 0
        row.blocked_until = None
    if row.count >= limit:
        if block_seconds:
            row.blocked_until = now + timedelta(seconds=block_seconds)
        return False
    row.count += 1
    if row.count >= limit and block_seconds:
        row.blocked_until = now + timedelta(seconds=block_seconds)
    return True


def register_login_failure(db: Session, user: User | None) -> None:
    if user is None:
        return
    user.failed_login_attempts = int(user.failed_login_attempts or 0) + 1
    if user.failed_login_attempts >= max(1, AUTH_MAX_LOGIN_ATTEMPTS):
        user.locked_until = utc_now() + timedelta(minutes=max(1, AUTH_LOCKOUT_MINUTES))


def clear_login_failures(user: User) -> None:
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = utc_now()


def token_is_usable(token: EmailToken, purpose: str) -> bool:
    if token.purpose != purpose or token.used_at is not None:
        return False
    expires = as_utc(token.expires_at)
    return expires is not None and expires > utc_now()


__all__ = [
    "AUTH_MAX_LOGIN_ATTEMPTS",
    "clear_login_failures",
    "clear_session_cookies",
    "compare_secret",
    "consume_rate_limit",
    "get_current_user",
    "get_optional_user",
    "get_db",
    "get_session_from_request",
    "hash_password",
    "hash_secret",
    "new_secret",
    "new_session",
    "normalize_email",
    "normalize_username",
    "register_login_failure",
    "require_owned_project",
    "require_owned_provider",
    "revoke_all_sessions",
    "revoke_session",
    "set_session_cookies",
    "set_csrf_cookie",
    "token_is_usable",
    "utc_now",
    "validate_csrf",
    "validate_origin",
    "validate_preauth_csrf",
    "validate_password",
    "verify_password",
]


# Public alias used by logout/change-password handlers without duplicating the
# cookie lookup rules.
get_session_from_request = _session_from_request
