"""Project backup and restore endpoints."""

from __future__ import annotations

import io
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Project
from ..services.exports import export_project_zip, restore_project_zip

router = APIRouter(prefix="/api/projects", tags=["exports"])


@router.get("/{project_id}/export")
def export_project(project_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        data = export_project_zip(db, project_id)
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
    file: UploadFile = File(...), project_id: str | None = None, db: Session = Depends(get_db)
) -> dict[str, str]:
    raw = file.file.read()
    try:
        project = restore_project_zip(db, raw, project_id=project_id)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": project.id, "name": project.name}
