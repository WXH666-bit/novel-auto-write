"""Focused regression tests for the durable generation/review workflow."""

from __future__ import annotations

import io
import zipfile

import pytest
from sqlalchemy.orm import Session, sessionmaker

from backend.app import models
from backend.app.db import Base, create_engine_for_url
from backend.app.services.exports import export_project_zip
from backend.app.services.generation import create_generation_run, execute_generation
from backend.app.services.importer import persist_import, preview_import
from backend.app.services.reviews import BlockerError, accept_review, reject_review


@pytest.fixture()
def db(tmp_path) -> Session:
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'workflow.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_import_decodes_and_splits_chinese_chapters(db: Session):
    project = models.Project(name="测试")
    db.add(project)
    db.commit()
    raw = "序章\n雾中的渡口\n\n第一章 初见\n林渡走进城门\n\n第2章 回声\n钟声响起".encode(
        "utf-8-sig"
    )
    preview = preview_import(raw, "故事.md")
    assert preview.encoding == "utf-8-sig"
    assert [chapter.title for chapter in preview.chapters] == ["序章", "第一章 初见", "第2章 回声"]
    created = persist_import(db, project, preview.chapters, source_hash=preview.source_hash)
    db.commit()
    assert len(created) == 3
    assert [chapter.chapter_number for chapter in created] == [1, 2, 3]
    assert all(chapter.current_revision_id for chapter in created)


def test_rejected_draft_does_not_change_canon(db: Session):
    project = models.Project(name="测试")
    db.add(project)
    db.commit()
    run = create_generation_run(db, project, {"idempotency_key": "reject-1"}).run
    execute_generation(db, run.id)
    bundle = db.query(models.ReviewBundle).one()
    before = (project.canon_version, db.query(models.CanonItem).count())
    reject_review(db, bundle.id, "需要重写")
    db.refresh(project)
    assert (project.canon_version, db.query(models.CanonItem).count()) == before
    assert bundle.status == "rejected"


def test_accept_is_atomic_and_blocker_requires_force_reason(db: Session):
    project = models.Project(name="测试")
    db.add(project)
    db.commit()
    run = create_generation_run(db, project, {"idempotency_key": "accept-1"}).run
    execute_generation(db, run.id)
    bundle = db.query(models.ReviewBundle).one()
    bundle.audit_issues = [{"code": "x", "severity": "blocker", "message": "冲突"}]
    bundle.canon_changes = [
        {"action": "create", "category": "人物", "key": "林渡.伤势", "value": "轻伤"}
    ]
    db.commit()
    with pytest.raises(BlockerError):
        accept_review(db, bundle.id)
    db.refresh(project)
    assert project.canon_version == 0
    accept_review(db, bundle.id, force_reason="编辑确认这是有意反转")
    db.refresh(project)
    assert project.canon_version == 1
    assert db.query(models.Chapter).one().current_revision_id == bundle.draft_revision_id


def test_generation_idempotency_and_export_excludes_keys(db: Session):
    project = models.Project(name="测试")
    db.add(project)
    db.commit()
    first = create_generation_run(db, project, {"idempotency_key": "same"})
    second = create_generation_run(db, project, {"idempotency_key": "same"})
    assert first.created is True
    assert second.created is False
    assert first.run.id == second.run.id
    data = export_project_zip(db, project.id)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "manifest.json" in names
    assert b"api_key" not in data.lower()
