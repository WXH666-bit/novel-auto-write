"""Authenticated project media upload, metadata, download, and deletion."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Character, MediaAsset, Project, User
from ..schemas import MediaAssetRead
from ..security import get_current_user, user_id_of
from ..services.media import (
    MediaValidationError,
    absolute_path,
    checksum,
    normalise_image,
    read_limited,
    safe_original_name,
    storage_key,
    validate_declared_mime,
    write_asset,
)
from . import require_project

router = APIRouter(prefix="/api", tags=["media"])


def _public(asset: MediaAsset) -> dict[str, object]:
    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "kind": asset.kind,
        "original_name": asset.original_name,
        "mime": asset.mime_type,
        "size": asset.byte_size,
        "checksum": asset.checksum,
        "width": asset.width,
        "height": asset.height,
        "alt": asset.alt_text,
        "created_at": asset.created_at,
        "download_url": f"/api/media/{asset.id}",
    }


def _asset(db: Session, asset_id: str, user: User) -> MediaAsset:
    asset = db.scalar(
        select(MediaAsset)
        .join(Project, Project.id == MediaAsset.project_id)
        .where(MediaAsset.id == asset_id, Project.owner_id == user_id_of(user))
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="媒体不存在")
    return asset


@router.get("/projects/{project_id}/media", response_model=list[MediaAssetRead])
def list_media(
    project_id: str,
    kind: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MediaAssetRead]:
    project = require_project(db, project_id, current_user)
    query = select(MediaAsset).where(MediaAsset.project_id == project.id)
    if kind:
        query = query.where(MediaAsset.kind == kind.strip().lower()[:40])
    rows = db.scalars(query.order_by(MediaAsset.created_at.desc())).all()
    return [MediaAssetRead.model_validate(_public(item)) for item in rows]


@router.post(
    "/projects/{project_id}/media",
    response_model=MediaAssetRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_media(
    project_id: str,
    file: UploadFile = File(...),
    kind: str = Form("character"),
    alt: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MediaAssetRead:
    project = require_project(db, project_id, current_user)
    try:
        raw = read_limited(file.file)
        data, mime_type, extension, width, height = normalise_image(raw)
        validate_declared_mime(file.content_type, mime_type)
    except MediaValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    normalized_kind = (kind or "character").strip().lower()[:40]
    if normalized_kind not in {"character", "image", "reference", "cover"}:
        raise HTTPException(status_code=422, detail="媒体类型不受支持")
    asset = MediaAsset(
        owner_id=user_id_of(current_user),
        project_id=project.id,
        kind=normalized_kind,
        original_name=safe_original_name(file.filename),
        mime_type=mime_type,
        extension=extension,
        byte_size=len(data),
        checksum=checksum(data),
        storage_key="",
        width=width,
        height=height,
        alt_text=(alt or "").strip()[:500] or None,
    )
    db.add(asset)
    db.flush()
    key = storage_key(user_id_of(current_user), project.id, asset.id, extension)
    asset.storage_key = key
    path: Path | None = None
    try:
        path = write_asset(key, data)
        db.commit()
    except Exception:
        db.rollback()
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    db.refresh(asset)
    return MediaAssetRead.model_validate(_public(asset))


@router.get("/media/{asset_id}", response_model=None)
def download_media(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    asset = _asset(db, asset_id, current_user)
    try:
        path = absolute_path(asset.storage_key)
    except MediaValidationError as exc:
        raise HTTPException(status_code=404, detail="媒体文件不存在") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    return FileResponse(
        path,
        media_type=asset.mime_type,
        filename=asset.original_name,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/media/{asset_id}/metadata", response_model=MediaAssetRead)
def media_metadata(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MediaAssetRead:
    return MediaAssetRead.model_validate(_public(_asset(db, asset_id, current_user)))


@router.delete("/media/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_media(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    asset = _asset(db, asset_id, current_user)
    if db.scalar(select(Character.id).where(Character.image_media_id == asset.id)) is not None:
        raise HTTPException(status_code=409, detail="该图片仍被人物卡片使用")
    try:
        path = absolute_path(asset.storage_key)
    except MediaValidationError:
        path = None
    db.delete(asset)
    db.commit()
    if path is not None:
        path.unlink(missing_ok=True)
