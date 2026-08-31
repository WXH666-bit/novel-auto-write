"""Application configuration.

The first release is deliberately local-only.  Configuration is read from
environment variables so the same code can be used by the Windows scripts
and by tests without putting secrets in a project database.
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
APP_HOST = os.getenv("NOVEL_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("NOVEL_PORT", "8000"))
DEBUG = os.getenv("NOVEL_DEBUG", "0").lower() in {"1", "true", "yes", "on"}

# Vite uses 5173 by default.  Keep localhost aliases because browsers treat
# 127.0.0.1 and localhost as different origins.
CORS_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
]
