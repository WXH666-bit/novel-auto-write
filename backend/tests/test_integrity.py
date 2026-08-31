"""Regression tests for the story-memory trust boundary."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.app import models
from backend.app.db import create_engine_for_url, init_db, rebuild_search_index
from backend.app.services.context import build_context
from backend.app.services.generation import create_generation_run, execute_generation
from backend.app.services.reviews import (
    BlockerError,
    ReviewValidationError,
    accept_review,
    edit_review_draft,
    reaudit_review_bundle,
)


def _session(tmp_path):
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'integrity.sqlite3').as_posix()}")
    init_db(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)()


def test_unaccepted_draft_is_excluded_from_context_and_fts(tmp_path):
    engine, db = _session(tmp_path)
    try:
        project = models.Project(name="记忆边界")
        db.add(project)
        db.flush()
        chapter = models.Chapter(
            project_id=project.id,
            chapter_number=1,
            sort_order=1,
            title="旧港",
            status="confirmed",
        )
        target = models.Chapter(
            project_id=project.id,
            chapter_number=2,
            sort_order=2,
            title="下一章",
            status="draft",
        )
        db.add_all([chapter, target])
        db.flush()
        accepted_text = "这是已经接受的旧港事实。"
        draft_text = "这是绝不能进入记忆的未审核草稿。"
        accepted = models.ChapterRevision(
            chapter_id=chapter.id,
            revision_number=1,
            content=accepted_text,
            content_hash=models.ChapterRevision.hash_content(accepted_text),
        )
        draft = models.ChapterRevision(
            chapter_id=chapter.id,
            revision_number=2,
            content=draft_text,
            content_hash=models.ChapterRevision.hash_content(draft_text),
        )
        db.add_all([accepted, draft])
        db.flush()
        chapter.accepted_revision_id = accepted.id
        chapter.current_revision_id = draft.id
        db.add(
            models.CanonItem(
                project_id=project.id,
                category="secret",
                key="待确认秘密",
                value="不能泄漏",
                value_text="不能泄漏",
                status="pending",
            )
        )
        db.commit()
        rebuild_search_index(db_engine=engine)

        context = build_context(db, project, target, query="旧港")
        assert "已经接受的旧港事实" in context["text"]
        assert "绝不能进入记忆" not in context["text"]
        assert "待确认秘密" not in context["text"]
        indexed = db.execute(text("SELECT revision_id, content FROM chapter_fts")).all()
        assert indexed == [(accepted.id, accepted.content)]
    finally:
        db.close()
        engine.dispose()


def test_idempotency_is_scoped_per_project(tmp_path):
    engine, db = _session(tmp_path)
    try:
        first = models.Project(name="甲")
        second = models.Project(name="乙")
        db.add_all([first, second])
        db.commit()
        run_a = create_generation_run(db, first, {"idempotency_key": "same-key"})
        run_b = create_generation_run(db, second, {"idempotency_key": "same-key"})
        assert run_a.created is True
        assert run_b.created is True
        assert run_a.run.project_id != run_b.run.project_id
    finally:
        db.close()
        engine.dispose()


def test_edited_review_requires_server_reaudit_and_high_severity_blocks(tmp_path):
    engine, db = _session(tmp_path)
    try:
        project = models.Project(name="审核边界")
        db.add(project)
        db.commit()
        run = create_generation_run(db, project, {"idempotency_key": "audit-edit"}).run
        execute_generation(db, run.id)
        bundle = db.query(models.ReviewBundle).one()
        edit_review_draft(db, bundle.id, "人工改写后的正文。")
        try:
            accept_review(db, bundle.id)
            raise AssertionError("编辑后的审核稿不应直接接受")
        except ReviewValidationError:
            pass
        reaudit_review_bundle(db, bundle.id)
        bundle.audit_issues = [{"severity": "high", "message": "高风险矛盾"}]
        db.commit()
        try:
            accept_review(db, bundle.id)
            raise AssertionError("high 严重度必须阻止普通接受")
        except BlockerError:
            pass
    finally:
        db.close()
        engine.dispose()
