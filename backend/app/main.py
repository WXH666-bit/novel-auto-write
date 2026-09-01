"""FastAPI application entry point for the local writing room."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import Response

from . import db as db_module
from .config import (
    APP_HOST,
    APP_PORT,
    CORS_ORIGINS,
    CSRF_COOKIE_NAME,
    DATABASE_URL,
    IS_PRODUCTION,
    MAIL_MODE,
    PUBLIC_BASE_URL,
    SESSION_COOKIE_SECURE,
    SMTP_HOST,
    TRUSTED_HOSTS,
)
from .routers.auth import router as auth_router
from .routers.canon import router as canon_router
from .routers.chapters import router as chapters_router
from .routers.exports import router as exports_router
from .routers.generations import router as generations_router
from .routers.imports import router as imports_router
from .routers.projects import router as projects_router
from .routers.providers import router as providers_router
from .routers.reviews import router as reviews_router
from .security import new_secret, set_csrf_cookie
from .services.generation import recover_incomplete_runs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def _validate_production_config() -> None:
    if not IS_PRODUCTION:
        return
    problems: list[str] = []
    if not PUBLIC_BASE_URL.startswith("https://"):
        problems.append("NOVEL_PUBLIC_BASE_URL 必须使用 https://")
    if not SESSION_COOKIE_SECURE:
        problems.append("生产会话 Cookie 必须启用 Secure")
    if not TRUSTED_HOSTS or "*" in TRUSTED_HOSTS:
        problems.append("NOVEL_TRUSTED_HOSTS 必须是明确域名")
    if not CORS_ORIGINS or "*" in CORS_ORIGINS:
        problems.append("NOVEL_CORS_ORIGINS 必须是明确 HTTPS Origin")
    if any(not origin.startswith("https://") for origin in CORS_ORIGINS):
        problems.append("生产 CORS Origin 必须使用 HTTPS")
    if not DATABASE_URL.startswith("mysql+pymysql://"):
        problems.append("生产数据库必须使用 mysql+pymysql:// 的 MySQL 8.4")
    if not SMTP_HOST:
        problems.append("生产环境必须配置 SMTP")
    if MAIL_MODE != "smtp":
        problems.append("生产环境 NOVEL_MAIL_MODE 必须为 smtp")
    if problems:
        raise RuntimeError("生产安全配置无效：" + "；".join(problems))


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if not request.cookies.get(CSRF_COOKIE_NAME):
            csrf_prefix = f"{CSRF_COOKIE_NAME}=".encode()
            already_set = any(
                name.lower() == b"set-cookie"
                and value.lower().startswith(csrf_prefix.lower())
                for name, value in response.raw_headers
            )
            if not already_set:
                set_csrf_cookie(response, new_secret())
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        sensitive_auth_page = request.url.path in {"/verify-email", "/reset-password"}
        response.headers.setdefault(
            "Referrer-Policy",
            "no-referrer" if sensitive_auth_page else "strict-origin-when-cross-origin",
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "object-src 'none'; form-action 'self'; img-src 'self' data: blob:; "
            "font-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'",
        )
        if IS_PRODUCTION:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if request.url.path.startswith(("/api/auth", "/api/providers")) or sensitive_auth_page:
            response.headers.setdefault("Cache-Control", "no-store")
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialise derived storage and make interrupted remote work explicit."""

    _validate_production_config()
    db_module.init_db()
    session = db_module.SessionLocal()
    try:
        recover_incomplete_runs(session)
    finally:
        session.close()
    yield


app = FastAPI(
    title="长篇小说连续性自动写作工作流",
    version="0.2.0",
    description="维护可追溯故事正典并提供严格租户隔离的写作 API。",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Content-Type", "X-CSRF-Token"],
)
if TRUSTED_HOSTS:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(projects_router)
app.include_router(auth_router)
app.include_router(chapters_router)
app.include_router(canon_router)
app.include_router(imports_router)
app.include_router(providers_router)
app.include_router(generations_router)
app.include_router(reviews_router)
app.include_router(exports_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "novel-auto-write"}


if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str) -> FileResponse:
    """Serve the built single-page app without masking missing API routes."""

    if full_path.startswith("api/") or not FRONTEND_DIST.is_dir():
        raise HTTPException(status_code=404, detail="资源不存在")
    requested = (FRONTEND_DIST / full_path).resolve()
    if FRONTEND_DIST.resolve() in requested.parents and requested.is_file():
        return FileResponse(requested)
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="前端尚未构建")
    return FileResponse(index)


if __name__ == "__main__":  # pragma: no cover - convenience for local use
    import uvicorn

    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=False)
