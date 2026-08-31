"""Review package inspection, draft editing, rejection, and atomic acceptance."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ReviewBundle
from ..services.providers import ProviderError
from ..services.reviews import (
    BlockerError,
    ReviewNotFound,
    ReviewValidationError,
    StaleReviewError,
    accept_review,
    bundle_payload,
    edit_review_draft,
    reaudit_review_bundle,
    reject_review,
)

router = APIRouter(prefix="/api", tags=["reviews"])


class DraftEditPayload(BaseModel):
    content: str = Field(min_length=1)
    actor: str = "editor"


class RejectPayload(BaseModel):
    reason: str = Field(min_length=1)
    actor: str = "editor"


class AcceptPayload(BaseModel):
    force_reason: str | None = None
    actor: str = "editor"


class ReauditPayload(BaseModel):
    actor: str = "editor"


@router.get("/projects/{project_id}/reviews")
def list_reviews(project_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    bundles = db.scalars(
        select(ReviewBundle)
        .where(ReviewBundle.project_id == project_id)
        .order_by(ReviewBundle.created_at.desc())
    ).all()
    return [bundle_payload(item) for item in bundles]


@router.get("/reviews/{bundle_id}")
def get_review(bundle_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        from ..services.reviews import _bundle

        return bundle_payload(_bundle(db, bundle_id))
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/reviews/{bundle_id}/draft")
def edit_draft(
    bundle_id: str, payload: DraftEditPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        return bundle_payload(
            edit_review_draft(db, bundle_id, payload.content, actor=payload.actor)
        )
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reviews/{bundle_id}/reaudit")
def reaudit(
    bundle_id: str, payload: ReauditPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        return bundle_payload(reaudit_review_bundle(db, bundle_id, actor=payload.actor))
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/reviews/{bundle_id}/reject")
def reject(bundle_id: str, payload: RejectPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return bundle_payload(reject_review(db, bundle_id, payload.reason, actor=payload.actor))
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reviews/{bundle_id}/accept")
def accept(bundle_id: str, payload: AcceptPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return bundle_payload(
            accept_review(db, bundle_id, force_reason=payload.force_reason, actor=payload.actor)
        )
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BlockerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StaleReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ReviewValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Compact aliases for clients that post the decision directly to the bundle.
@router.post("/reviews/{bundle_id}/decision")
def decision(
    bundle_id: str, payload: dict[str, Any], db: Session = Depends(get_db)
) -> dict[str, Any]:
    action = str(payload.get("action") or "").lower()
    if action == "accept":
        return accept(
            bundle_id,
            AcceptPayload(
                force_reason=payload.get("force_reason"),
                actor=str(payload.get("actor") or "editor"),
            ),
            db,
        )
    if action == "reject":
        return reject(
            bundle_id,
            RejectPayload(
                reason=str(payload.get("reason") or ""), actor=str(payload.get("actor") or "editor")
            ),
            db,
        )
    raise HTTPException(status_code=422, detail="action 必须为 accept 或 reject")
