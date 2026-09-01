"""Regression tests for one-at-a-time multi-chapter generation batches."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from backend.app import models
from backend.app.db import Base, create_engine_for_url
from backend.app.schemas import GenerationRequest
from backend.app.services.generation import (
    create_generation_run,
    execute_generation,
    reconcile_batch_next_run,
    run_snapshot,
)
from backend.app.services.reviews import accept_review, reject_review
from backend.tests.helpers import install_fake_provider, seed_tenant


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Session:
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'batch.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    user, _ = seed_tenant(session)
    session.info["test_user_id"] = user.id
    install_fake_provider(monkeypatch)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _project(db: Session) -> models.Project:
    project = models.Project(owner_id=db.info["test_user_id"], name="连续批次")
    db.add(project)
    db.commit()
    return project


def test_generation_request_limits_batch_size() -> None:
    assert GenerationRequest(idempotency_key="one").chapter_count == 1
    assert GenerationRequest(idempotency_key="many", chapter_count=10).chapter_count == 10
    with pytest.raises(ValidationError):
        GenerationRequest(idempotency_key="zero", chapter_count=0)
    with pytest.raises(ValidationError):
        GenerationRequest(idempotency_key="too-many", chapter_count=11)


def test_batch_queues_only_after_accept_and_rebuilds_child_context(db: Session) -> None:
    project = _project(db)
    result = create_generation_run(
        db,
        project,
        {
            "idempotency_key": "continuous-1",
            "chapter_count": 3,
            "mode": "next_chapter",
        },
    )
    run = result.run
    assert db.query(models.Chapter).count() == 1
    assert db.query(models.GenerationRun).count() == 1
    assert run_snapshot(run)["batch_remaining"] == 2

    execute_generation(db, run.id)
    bundle = db.query(models.ReviewBundle).one()
    revision = db.get(models.ChapterRevision, bundle.draft_revision_id)
    assert revision is not None
    excerpt = revision.content[:8]
    bundle.canon_changes = [
        {
            "action": "create",
            "category": "线索",
            "key": "灯塔.盐霜",
            "value": "门轴留有盐霜",
            "source_start": 0,
            "source_end": len(excerpt),
            "source_excerpt": excerpt,
        }
    ]
    db.commit()

    accept_review(db, bundle.id)
    child = db.query(models.GenerationRun).filter(models.GenerationRun.id != run.id).one()
    assert db.query(models.Chapter).count() == 2
    assert child.status == "queued"
    assert child.provider_profile_id == run.provider_profile_id
    assert child.provider_protocol == run.provider_protocol
    assert child.provider_snapshot == run.provider_snapshot
    assert "api_key" not in child.provider_snapshot
    assert "context_snapshot" not in child.input_snapshot
    assert not child.context_snapshot
    child_snapshot = run_snapshot(child)
    assert child_snapshot["batch_id"] == run_snapshot(run)["batch_id"]
    assert child_snapshot["batch_index"] == 2
    assert child_snapshot["batch_total"] == 3
    assert child_snapshot["batch_remaining"] == 1

    # Replaying acceptance/reconciliation returns the same durable child.
    accept_review(db, bundle.id)
    assert reconcile_batch_next_run(db, run.id).id == child.id
    assert db.query(models.GenerationRun).count() == 2
    assert db.query(models.Chapter).count() == 2

    execute_generation(db, child.id)
    db.refresh(child)
    assert child.status == "awaiting_review"
    assert any(
        source.get("kind") == "canon" and "灯塔.盐霜" in source.get("excerpt", "")
        for source in child.context_snapshot.get("sources", [])
    )


def test_child_generation_pauses_when_memory_needs_rebuild(db: Session) -> None:
    project = _project(db)
    run = create_generation_run(
        db,
        project,
        {
            "idempotency_key": "continuous-rebuild-gate",
            "chapter_count": 2,
            "mode": "next_chapter",
        },
    ).run
    execute_generation(db, run.id)
    bundle = db.query(models.ReviewBundle).one()
    accept_review(db, bundle.id)

    child = (
        db.query(models.GenerationRun)
        .filter(models.GenerationRun.id != run.id)
        .one()
    )
    assert child.status == "queued"
    project.needs_rebuild = True
    db.commit()

    execute_generation(db, child.id)
    db.refresh(child)
    job = db.get(models.Job, child.job_id)
    assert child.status == "needs_retry"
    assert job is not None
    assert job.state == "needs_retry"
    assert child.review_bundle_id is None
    assert (
        db.query(models.ReviewBundle)
        .filter(models.ReviewBundle.generation_run_id == child.id)
        .count()
        == 0
    )


def test_custom_first_chapter_title_is_not_copied_to_batch_child(db: Session) -> None:
    project = _project(db)
    run = create_generation_run(
        db,
        project,
        {
            "idempotency_key": "continuous-custom-title",
            "chapter_count": 2,
            "mode": "next_chapter",
            "title": "自定义首章",
        },
    ).run
    first = db.get(models.Chapter, run.chapter_id)
    assert first is not None
    assert first.title == "自定义首章"

    execute_generation(db, run.id)
    bundle = db.query(models.ReviewBundle).one()
    accept_review(db, bundle.id)

    child = (
        db.query(models.GenerationRun)
        .filter(models.GenerationRun.id != run.id)
        .one()
    )
    second = db.get(models.Chapter, child.chapter_id)
    assert second is not None
    assert second.chapter_number == 2
    assert second.title == "第2章"
    assert second.title != first.title


def test_rejected_batch_does_not_create_next_chapter(db: Session) -> None:
    project = _project(db)
    run = create_generation_run(
        db,
        project,
        {"idempotency_key": "continuous-reject", "chapter_count": 2},
    ).run
    execute_generation(db, run.id)
    bundle = db.query(models.ReviewBundle).one()
    reject_review(db, bundle.id, "需要重写")
    assert db.query(models.GenerationRun).count() == 1
    assert db.query(models.Chapter).count() == 1


def test_non_chapter_modes_reject_multi_chapter_request(db: Session) -> None:
    project = _project(db)
    with pytest.raises(ValueError):
        create_generation_run(
            db,
            project,
            {
                "idempotency_key": "rewrite-batch",
                "chapter_count": 2,
                "mode": "rewrite",
            },
        )
    assert db.query(models.GenerationRun).count() == 0
    assert db.query(models.Chapter).count() == 0
