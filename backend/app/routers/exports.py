"""Project backup and restore endpoints."""

from __future__ import annotations

import io
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_db, rebuild_search_index
from ..models import User
from ..security import get_current_user
from ..services.exports import export_project_zip, restore_project_zip
from . import require_project

router = APIRouter(prefix="/api/projects", tags=["exports"])
MAX_BACKUP_BYTES = 256 * 1024 * 1024


@router.get("/{project_id}/export")
def export_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    require_project(db, project_id, current_user)
    try:
        data = export_project_zip(db, project_id, owner_id=current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = f"novel-project-{project_id[:8]}-{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/restore")
def restore_project(
    file: UploadFile = File(...),
    project_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if project_id:
        require_project(db, project_id, current_user)
    raw = file.file.read(MAX_BACKUP_BYTES + 1)
    if len(raw) > MAX_BACKUP_BYTES:
        raise HTTPException(status_code=413, detail="项目备份不能超过 256 MB")
    try:
        project = restore_project_zip(
            db,
            raw,
            owner_id=current_user.id,
            project_id=project_id,
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        rebuild_search_index(
            db_engine=db.get_bind(),
            owner_id=current_user.id,
            project_id=project.id,
        )
    except Exception:
        # Search is derived state and is rebuilt again on the next startup.
        pass
    return {"id": project.id, "name": project.name}
