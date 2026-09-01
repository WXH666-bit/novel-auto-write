"""Project-isolated character cards and append-only card revisions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    AuditLog,
    Character,
    CharacterRevision,
    MediaAsset,
    Project,
    StoryGraphEdge,
    StoryGraphNode,
    User,
)
from ..schemas import (
    CharacterCreate,
    CharacterRead,
    CharacterRevisionRead,
    CharacterUpdate,
    MediaAssetRead,
)
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

router = APIRouter(prefix="/api", tags=["characters"])

CHARACTER_FIELDS = (
    "name",
    "aliases",
    "role",
    "gender",
    "pronouns",
    "age",
    "occupation",
    "appearance",
    "personality",
    "background",
    "goals",
    "motivation",
    "conflict_fears",
    "abilities",
    "tags",
    "arc",
    "voice",
    "status",
    "custom_fields",
    "image_media_id",
)


def _normalise_values(values: dict[str, Any]) -> dict[str, Any]:
    # Compact forms often call these fields ``goal`` and ``conflict``.  Keep
    # the durable schema descriptive while accepting both spellings.
    if values.get("goals") is None and values.get("goal") is not None:
        values["goals"] = values["goal"]
    values.pop("goal", None)
    if values.get("conflict_fears") is None and values.get("conflict") is not None:
        values["conflict_fears"] = values["conflict"]
    values.pop("conflict", None)
    return {key: value for key, value in values.items() if key in CHARACTER_FIELDS}


def _media_for_character(db: Session, project: Project, user: User, media_id: str | None) -> None:
    if not media_id:
        return
    asset = db.scalar(
        select(MediaAsset).where(
            MediaAsset.id == media_id,
            MediaAsset.project_id == project.id,
            MediaAsset.owner_id == user_id_of(user),
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="人物图片不存在或不属于当前项目")
    if asset.kind not in {"character", "image"}:
        raise HTTPException(status_code=422, detail="该媒体不是可用于人物卡片的图片")


def _next_revision_number(db: Session, character_id: str) -> int:
    latest = db.scalar(
        select(func.max(CharacterRevision.revision_number)).where(
            CharacterRevision.character_id == character_id
        )
    )
    return int(latest or 0) + 1


def _revision_from_character(
    db: Session,
    character: Character,
    user: User,
    *,
    source_type: str,
    source_revision_id: str | None = None,
) -> CharacterRevision:
    revision = CharacterRevision(
        character_id=character.id,
        revision_number=_next_revision_number(db, character.id),
        **{
            field: getattr(character, field)
            for field in CHARACTER_FIELDS
            if field != "image_media_id" or hasattr(character, field)
        },
        source_type=source_type[:40],
        source_revision_id=source_revision_id,
        created_by_user_id=user_id_of(user),
    )
    db.add(revision)
    db.flush()
    character.current_revision_id = revision.id
    return revision


def _character_payload(character: Character) -> CharacterRead:
    return CharacterRead.model_validate(character)


def _require_character(db: Session, project: Project, character_id: str) -> Character:
    character = db.scalar(
        select(Character).where(Character.id == character_id, Character.project_id == project.id)
    )
    if character is None:
        raise HTTPException(status_code=404, detail="人物卡片不存在")
    return character


def _audit(
    db: Session,
    user: User,
    project: Project,
    action: str,
    character: Character,
    *,
    before: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            project_id=project.id,
            actor_user_id=user_id_of(user),
            actor=user.username or user.email or user.id,
            action=action,
            entity_type="character",
            entity_id=character.id,
            before_json=before,
            after_json={
                "name": character.name,
                "version": character.version,
                "current_revision_id": character.current_revision_id,
            },
        )
    )


def _character_node(db: Session, project: Project, character: Character) -> StoryGraphNode:
    node = db.scalar(
        select(StoryGraphNode).where(
            StoryGraphNode.project_id == project.id,
            StoryGraphNode.node_type == "character",
            StoryGraphNode.ref_id == character.id,
        )
    )
    if node is None:
        node = StoryGraphNode(
            project_id=project.id,
            node_type="character",
            ref_id=character.id,
            character_id=character.id,
            label=character.name,
            data={"source": "character_card"},
        )
        db.add(node)
    elif node.label != character.name:
        node.label = character.name
        node.version += 1
    return node


@router.get("/projects/{project_id}/characters", response_model=list[CharacterRead])
def list_characters(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CharacterRead]:
    project = require_project(db, project_id, current_user)
    characters = db.scalars(
        select(Character).where(Character.project_id == project.id).order_by(Character.created_at)
    ).all()
    return [_character_payload(character) for character in characters]


@router.post(
    "/projects/{project_id}/characters",
    response_model=CharacterRead,
    status_code=status.HTTP_201_CREATED,
)
def create_character(
    project_id: str,
    payload: CharacterCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CharacterRead:
    project = require_project(db, project_id, current_user)
    values = _normalise_values(payload.model_dump())
    _media_for_character(db, project, current_user, values.get("image_media_id"))
    character = Character(project_id=project.id, **values)
    db.add(character)
    try:
        db.flush()
        _revision_from_character(
            db, character, current_user, source_type=str(payload.source_type or "manual")
        )
        _character_node(db, project, character)
        project.memory_epoch = int(project.memory_epoch or 0) + 1
        _audit(db, current_user, project, "character.created", character)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="当前项目中已有同名人物卡片") from exc
    db.refresh(character)
    return _character_payload(character)


@router.get("/projects/{project_id}/characters/{character_id}", response_model=CharacterRead)
def get_character(
    project_id: str,
    character_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CharacterRead:
    project = require_project(db, project_id, current_user)
    return _character_payload(_require_character(db, project, character_id))


@router.patch("/projects/{project_id}/characters/{character_id}", response_model=CharacterRead)
def update_character(
    project_id: str,
    character_id: str,
    payload: CharacterUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CharacterRead:
    project = require_project(db, project_id, current_user)
    character = db.scalar(
        select(Character)
        .where(Character.id == character_id, Character.project_id == project.id)
        .with_for_update()
    )
    if character is None:
        raise HTTPException(status_code=404, detail="人物卡片不存在")
    expected = payload.expected_version
    if expected is not None and expected != character.version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "character_conflict",
                "message": "人物卡片已在其他窗口更新，请刷新后重试",
                "expected_version": expected,
                "actual_version": character.version,
            },
        )
    before = {field: getattr(character, field) for field in CHARACTER_FIELDS}
    values = _normalise_values(payload.model_dump(exclude_unset=True))
    values.pop("name", None) if values.get("name") == character.name else None
    _media_for_character(db, project, current_user, values.get("image_media_id"))
    if not values:
        return _character_payload(character)
    for field, value in values.items():
        setattr(character, field, value)
    character.version += 1
    _character_node(db, project, character)
    source_type = str(payload.source_type or "manual")
    _revision_from_character(db, character, current_user, source_type=source_type)
    project.memory_epoch = int(project.memory_epoch or 0) + 1
    _audit(db, current_user, project, "character.updated", character, before=before)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="当前项目中已有同名人物卡片") from exc
    db.refresh(character)
    return _character_payload(character)


@router.delete("/projects/{project_id}/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character(
    project_id: str,
    character_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    project = require_project(db, project_id, current_user)
    character = _require_character(db, project, character_id)
    db.add(
        AuditLog(
            project_id=project.id,
            actor_user_id=user_id_of(current_user),
            actor=current_user.username or current_user.email or current_user.id,
            action="character.deleted",
            entity_type="character",
            entity_id=character.id,
            before_json={"name": character.name, "version": character.version},
        )
    )
    node = db.scalar(
        select(StoryGraphNode).where(
            StoryGraphNode.project_id == project.id,
            StoryGraphNode.node_type == "character",
            StoryGraphNode.ref_id == character.id,
        )
    )
    if node is not None:
        db.query(StoryGraphEdge).filter(
            (StoryGraphEdge.source_node_id == node.id)
            | (StoryGraphEdge.target_node_id == node.id)
        ).delete(synchronize_session=False)
        db.delete(node)
    db.delete(character)
    project.memory_epoch = int(project.memory_epoch or 0) + 1
    db.commit()


@router.get(
    "/projects/{project_id}/characters/{character_id}/revisions",
    response_model=list[CharacterRevisionRead],
)
def list_character_revisions(
    project_id: str,
    character_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CharacterRevisionRead]:
    project = require_project(db, project_id, current_user)
    character = _require_character(db, project, character_id)
    revisions = db.scalars(
        select(CharacterRevision)
        .where(CharacterRevision.character_id == character.id)
        .order_by(CharacterRevision.revision_number)
    ).all()
    return [CharacterRevisionRead.model_validate(item) for item in revisions]


def _direct_character(db: Session, character_id: str, user: User) -> Character:
    character = db.scalar(
        select(Character)
        .join(Project, Project.id == Character.project_id)
        .where(Character.id == character_id, Project.owner_id == user_id_of(user))
    )
    if character is None:
        raise HTTPException(status_code=404, detail="人物卡片不存在")
    return character


def _portrait_asset(db: Session, character: Character, user: User) -> MediaAsset | None:
    if not character.image_media_id:
        return None
    return db.scalar(
        select(MediaAsset).where(
            MediaAsset.id == character.image_media_id,
            MediaAsset.project_id == character.project_id,
            MediaAsset.owner_id == user_id_of(user),
            MediaAsset.kind.in_(("character", "image")),
        )
    )


def _portrait_public(asset: MediaAsset, character: Character) -> MediaAssetRead:
    return MediaAssetRead.model_validate(
        {
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
            "download_url": f"/api/characters/{character.id}/portrait",
        }
    )


def _save_portrait(
    db: Session,
    character: Character,
    user: User,
    file: UploadFile,
    alt: str | None,
) -> MediaAssetRead:
    try:
        raw = read_limited(file.file)
        data, mime_type, extension, width, height = normalise_image(raw)
        validate_declared_mime(file.content_type, mime_type)
    except MediaValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    old_asset = _portrait_asset(db, character, user)
    old_path = None
    if old_asset is not None:
        try:
            old_path = absolute_path(old_asset.storage_key)
        except MediaValidationError:
            old_path = None
    asset = MediaAsset(
        owner_id=user_id_of(user),
        project_id=character.project_id,
        kind="character",
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
    key = storage_key(user_id_of(user), character.project_id, asset.id, extension)
    asset.storage_key = key
    path = None
    try:
        path = write_asset(key, data)
        character.image_media_id = asset.id
        character.version += 1
        _revision_from_character(db, character, user, source_type="portrait")
        project = db.get(Project, character.project_id)
        if project is not None:
            project.memory_epoch = int(project.memory_epoch or 0) + 1
            _audit(db, user, project, "character.portrait_uploaded", character)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if path is not None:
            path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="人物画像保存冲突，请重试") from exc
    except Exception:
        db.rollback()
        if path is not None:
            path.unlink(missing_ok=True)
        raise

    # The replacement is committed first.  Only then remove the old row/file,
    # and only if no other card still references it.
    if old_asset is not None and old_asset.id != asset.id:
        still_used = db.scalar(
            select(Character.id).where(Character.image_media_id == old_asset.id)
        )
        old_asset_removed = False
        if still_used is None:
            db.delete(old_asset)
            try:
                db.commit()
                old_asset_removed = True
            except Exception:
                db.rollback()
        # A media row may intentionally be shared by more than one card.  Do
        # not remove its bytes while another character (or a failed cleanup
        # transaction) can still reference them.
        if old_asset_removed and old_path is not None:
            old_path.unlink(missing_ok=True)
    db.refresh(asset)
    return _portrait_public(asset, character)


@router.post(
    "/characters/{character_id}/portrait",
    response_model=MediaAssetRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_character_portrait(
    character_id: str,
    file: UploadFile = File(...),
    alt: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MediaAssetRead:
    character = _direct_character(db, character_id, current_user)
    return _save_portrait(db, character, current_user, file, alt)


@router.get("/characters/{character_id}/portrait", response_model=None)
def get_character_portrait(
    character_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    character = _direct_character(db, character_id, current_user)
    asset = _portrait_asset(db, character, current_user)
    if asset is None:
        raise HTTPException(status_code=404, detail="人物尚未设置画像")
    try:
        path = absolute_path(asset.storage_key)
    except MediaValidationError as exc:
        raise HTTPException(status_code=404, detail="人物画像文件不存在") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="人物画像文件不存在")
    return FileResponse(
        path,
        media_type=asset.mime_type,
        filename=asset.original_name,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/characters/{character_id}/portrait/metadata", response_model=MediaAssetRead)
def get_character_portrait_metadata(
    character_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MediaAssetRead:
    character = _direct_character(db, character_id, current_user)
    asset = _portrait_asset(db, character, current_user)
    if asset is None:
        raise HTTPException(status_code=404, detail="人物尚未设置画像")
    return _portrait_public(asset, character)


@router.delete("/characters/{character_id}/portrait", status_code=status.HTTP_204_NO_CONTENT)
def delete_character_portrait(
    character_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    character = _direct_character(db, character_id, current_user)
    asset = _portrait_asset(db, character, current_user)
    if asset is None:
        # Clear a stale pointer as a repair operation, while keeping DELETE
        # idempotent for clients removing an already-removed portrait.
        if character.image_media_id:
            character.image_media_id = None
            character.version += 1
            _revision_from_character(db, character, current_user, source_type="portrait_remove")
            db.commit()
        return
    try:
        path = absolute_path(asset.storage_key)
    except MediaValidationError:
        path = None
    character.image_media_id = None
    character.version += 1
    _revision_from_character(db, character, current_user, source_type="portrait_remove")
    project = db.get(Project, character.project_id)
    if project is not None:
        project.memory_epoch = int(project.memory_epoch or 0) + 1
        _audit(db, current_user, project, "character.portrait_deleted", character)
    db.delete(asset)
    db.commit()
    if path is not None:
        path.unlink(missing_ok=True)


@router.get("/characters/{character_id}", response_model=CharacterRead, include_in_schema=False)
def get_character_direct(
    character_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CharacterRead:
    return _character_payload(_direct_character(db, character_id, current_user))


@router.patch("/characters/{character_id}", response_model=CharacterRead, include_in_schema=False)
def update_character_direct(
    character_id: str,
    payload: CharacterUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CharacterRead:
    character = _direct_character(db, character_id, current_user)
    return update_character(
        character.project_id, character_id, payload, current_user=current_user, db=db
    )


@router.delete("/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
def delete_character_direct(
    character_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    character = _direct_character(db, character_id, current_user)
    delete_character(character.project_id, character_id, current_user=current_user, db=db)
