"""Single-host local/production configuration.

Configuration is read from environment variables so Windows can use SQLite
while a hardened Linux deployment uses MySQL, SMTP, and Secret Service.  No
user Provider credential is accepted through process-wide environment
variables or persisted in the project database.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("NOVEL_DATA_DIR", str(PROJECT_ROOT / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _database_url() -> str:
    configured = os.getenv("NOVEL_DATABASE_URL")
    if configured:
        return configured
    return f"sqlite:///{(DATA_DIR / 'novel.sqlite3').as_posix()}"


DATABASE_URL = _database_url()
JOB_WORKERS = int(
    # Remote model calls are I/O-bound, so a small thread pool improves MySQL
    # throughput without tying the default to the host's CPU count.  SQLite
    # remains single-worker because its write lock is database-wide.
    os.getenv(
        "NOVEL_JOB_WORKERS",
        "4" if DATABASE_URL.strip().lower().startswith("mysql") else "1",
    )
)
if JOB_WORKERS < 1:
    raise ValueError("NOVEL_JOB_WORKERS 必须至少为 1")
if JOB_WORKERS > 32:
    raise ValueError("NOVEL_JOB_WORKERS 不能超过 32")
APP_HOST = os.getenv("NOVEL_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("NOVEL_PORT", "8000"))
DEBUG = os.getenv("NOVEL_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
ENVIRONMENT = os.getenv("NOVEL_ENV", "local").strip().lower()
IS_PRODUCTION = ENVIRONMENT in {"production", "prod"}

# Authentication is selected once by the deployment operator.  ``email`` is
# the backwards-compatible default; ``username`` deliberately has no email
# dependency, so it can run without an SMTP service.
AUTH_MODE = os.getenv("NOVEL_AUTH_MODE", "email").strip().lower()
if AUTH_MODE not in {"email", "username"}:
    raise ValueError("NOVEL_AUTH_MODE 必须为 email 或 username")


def _csv_env(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]

# Vite uses 5173 by default.  Keep localhost aliases because browsers treat
# 127.0.0.1 and localhost as different origins.
CORS_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
]
configured_cors = _csv_env("NOVEL_CORS_ORIGINS")
if configured_cors:
    CORS_ORIGINS = configured_cors

TRUSTED_HOSTS = _csv_env(
    "NOVEL_TRUSTED_HOSTS",
    "localhost,127.0.0.1" if not IS_PRODUCTION else "",
)
PUBLIC_BASE_URL = os.getenv(
    "NOVEL_PUBLIC_BASE_URL",
    os.getenv("NOVEL_PUBLIC_URL", "http://127.0.0.1:8000"),
).rstrip("/")

# Authentication/session settings.  Session values are opaque random strings;
# only their SHA-256 digests are persisted in SQLite/MySQL.
SESSION_COOKIE_NAME = os.getenv("NOVEL_SESSION_COOKIE", "novel_session")
CSRF_COOKIE_NAME = os.getenv("NOVEL_CSRF_COOKIE", "novel_csrf")
SESSION_TTL_DAYS = int(os.getenv("NOVEL_SESSION_TTL_DAYS", "30"))
SESSION_COOKIE_SECURE = os.getenv(
    "NOVEL_SESSION_COOKIE_SECURE",
    os.getenv("NOVEL_COOKIE_SECURE", "1" if IS_PRODUCTION else "0"),
).lower() in {"1", "true", "yes", "on"}
if IS_PRODUCTION:
    SESSION_COOKIE_SECURE = True
CSRF_ENFORCE = os.getenv(
    "NOVEL_CSRF_ENFORCE", "1" if IS_PRODUCTION else "0"
).lower() in {"1", "true", "yes", "on"}
if IS_PRODUCTION:
    CSRF_ENFORCE = True
AUTH_MAX_LOGIN_ATTEMPTS = int(os.getenv("NOVEL_AUTH_MAX_LOGIN_ATTEMPTS", "8"))
AUTH_LOCKOUT_MINUTES = int(os.getenv("NOVEL_AUTH_LOCKOUT_MINUTES", "15"))
AUTH_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("NOVEL_AUTH_RATE_LIMIT_WINDOW_SECONDS", "900"))

# Email delivery.  Local development can point these values at Mailpit, while
# production uses a conventional authenticated SMTP relay.
SMTP_HOST = os.getenv("NOVEL_SMTP_HOST", "127.0.0.1")
SMTP_EXPLICITLY_CONFIGURED = bool(os.getenv("NOVEL_SMTP_HOST", "").strip())
SMTP_PORT = int(os.getenv("NOVEL_SMTP_PORT", "1025"))
SMTP_USERNAME = os.getenv("NOVEL_SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("NOVEL_SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("NOVEL_SMTP_FROM", "novel-auto-write@localhost")
SMTP_USE_TLS = os.getenv(
    "NOVEL_SMTP_USE_TLS", os.getenv("NOVEL_SMTP_STARTTLS", "0")
).lower() in {"1", "true", "yes", "on"}
MAIL_MODE = os.getenv("NOVEL_MAIL_MODE", "smtp").strip().lower()

# A stable, non-login owner used only when upgrading an old single-user DB.
# It is never automatically assigned to the first public account.
LEGACY_OWNER_ID = os.getenv("NOVEL_LEGACY_OWNER_ID", "00000000-0000-0000-0000-000000000001")

# Provider URL policy.  Official hosts are allowed by default.  Operators can
# append approved gateways through NOVEL_ALLOWED_PROVIDER_HOSTS.  Local mode
# may explicitly enable loopback/local model servers.
ALLOWED_PROVIDER_HOSTS = _csv_env(
    "NOVEL_ALLOWED_PROVIDER_HOSTS",
    os.getenv("NOVEL_PROVIDER_ALLOWED_HOSTS", "api.openai.com,api.anthropic.com"),
)
ALLOW_LOCAL_PROVIDER_HOSTS = os.getenv(
    "NOVEL_ALLOW_LOCAL_PROVIDER_HOSTS", "1" if not IS_PRODUCTION else "0"
).lower() in {"1", "true", "yes", "on"}
