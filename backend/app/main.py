"""FastAPI application entry point for the local writing room."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db as db_module
from .config import APP_HOST, APP_PORT, CORS_ORIGINS
from .routers.canon import router as canon_router
from .routers.chapters import router as chapters_router
from .routers.exports import router as exports_router
from .routers.generations import router as generations_router
from .routers.imports import router as imports_router
from .routers.projects import router as projects_router
from .routers.providers import router as providers_router
from .routers.reviews import router as reviews_router
from .services.generation import recover_incomplete_runs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialise derived storage and make interrupted remote work explicit."""

    db_module.init_db()
    session = db_module.SessionLocal()
    try:
        recover_incomplete_runs(session)
    finally:
        session.close()
    yield


app = FastAPI(
    title="长篇小说连续性自动写作工作流",
    version="0.1.0",
    description="维护可追溯故事正典的本地单机 API。",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(projects_router)
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
