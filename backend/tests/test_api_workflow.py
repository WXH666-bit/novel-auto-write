"""End-to-end API checks for the user-visible writing workflow."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from backend.app import db as db_module
from backend.app import models
from backend.app.db import create_engine_for_url, get_db, init_db
from backend.app.main import app
from backend.app.routers import imports as imports_router
from backend.app.services import exports as export_service
from backend.tests.helpers import authenticate_client, install_fake_provider


def _client(tmp_path, monkeypatch, *, with_provider: bool = True) -> TestClient:
    test_engine = create_engine_for_url(
        f"sqlite:///{(tmp_path / 'api-workflow.sqlite3').as_posix()}"
    )
    init_db(test_engine)
    testing_session = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", testing_session)
    monkeypatch.setattr(imports_router, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(export_service, "DATA_DIR", tmp_path)

    def override_db():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    install_fake_provider(monkeypatch)
    client = TestClient(app, base_url="http://127.0.0.1")
    authenticate_client(client, testing_session, with_provider=with_provider)
    return client


def _generate(client: TestClient, project_id: str, chapter_id: str, key: str) -> dict:
    created = client.post(
        f"/api/projects/{project_id}/generations",
        json={
            "chapter_id": chapter_id,
            "idempotency_key": key,
            "target_word_count": 800,
            "instructions": "推进灯塔失火的线索，但不要揭示幕后人物。",
            "mode": "quality",
        },
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    snapshot = client.get(f"/api/generations/{run_id}")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["status"] == "awaiting_review", snapshot.json()
    assert snapshot.json()["review_bundle_id"]
    return snapshot.json()


def test_import_generate_accept_and_reject_are_atomic(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        project_response = client.post(
            "/api/projects",
            json={"title": "雾港来信", "genre": "悬疑", "style": "克制、具体"},
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]

        preview_response = client.post(
            f"/api/projects/{project_id}/import/preview",
            files={
                "file": (
                    "old-story.txt",
                    "第一章 雾港\n林渡抵达旧港。\n\n第二章 灯塔\n守灯人拒绝开门。".encode(),
                    "text/plain",
                )
            },
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        assert preview["encoding"] == "utf-8"
        assert len(preview["chapters"]) == 2

        commit_response = client.post(
            f"/api/projects/{project_id}/import/commit",
            json={
                "filename": preview["filename"],
                "source_hash": preview["source_hash"],
                "encoding": preview["encoding"],
                "chapters": preview["chapters"],
            },
        )
        assert commit_response.status_code == 200, commit_response.text
        chapters = commit_response.json()["chapters"]
        assert len(chapters) == 2
        assert chapters[0]["current_revision"]["content"] == "林渡抵达旧港。"
        exported = client.get(f"/api/projects/{project_id}/export")
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            assert any(name.startswith("original_imports/") for name in archive.namelist())
        chapter_id = chapters[-1]["id"]

        accepted_run = _generate(client, project_id, chapter_id, "api-accept-1")
        replay = client.post(
            f"/api/projects/{project_id}/generations",
            json={
                "chapter_id": chapter_id,
                "idempotency_key": "api-accept-1",
                "target_word_count": 800,
                "instructions": "推进灯塔失火的线索，但不要揭示幕后人物。",
                "mode": "quality",
            },
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == accepted_run["id"]
        assert replay.json()["created"] is False

        before_accept = client.get(f"/api/projects/{project_id}").json()["canon_version"]
        bypass_review = client.post(f"/api/chapters/{chapter_id}/confirm")
        assert bypass_review.status_code == 409
        assert client.get(f"/api/projects/{project_id}").json()["canon_version"] == before_accept
        accepted = client.post(
            f"/api/reviews/{accepted_run['review_bundle_id']}/accept",
            json={},
        )
        assert accepted.status_code == 200, accepted.text
        after_accept = client.get(f"/api/projects/{project_id}").json()["canon_version"]
        assert after_accept == before_accept + 1
        with db_module.SessionLocal() as db:
            accepted_revision_id = db.get(models.Chapter, chapter_id).accepted_revision_id
            indexed_after_accept = db.execute(
                text(
                    "SELECT revision_id, content FROM chapter_fts "
                    "WHERE project_id = :project_id AND chapter_id = :chapter_id"
                ),
                {"project_id": project_id, "chapter_id": chapter_id},
            ).one()
        assert indexed_after_accept[0] == accepted_revision_id
        assert "雾港旧堤" in indexed_after_accept[1]

        rejected_run = _generate(client, project_id, chapter_id, "api-reject-1")
        rejected = client.post(
            f"/api/reviews/{rejected_run['review_bundle_id']}/reject",
            json={"reason": "本章节奏不符合预期"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        assert client.get(f"/api/projects/{project_id}").json()["canon_version"] == after_accept

        stale_run = _generate(client, project_id, chapter_id, "api-stale-edit-1")
        edited = client.patch(
            f"/api/chapters/{chapter_id}",
            json={"content": "用户在审核期间手动重写了这一章。"},
        )
        assert edited.status_code == 200, edited.text
        stale_bundle = client.get(f"/api/reviews/{stale_run['review_bundle_id']}")
        assert stale_bundle.status_code == 200
        assert stale_bundle.json()["status"] == "stale"
        previously_accepted = client.get(
            f"/api/reviews/{accepted_run['review_bundle_id']}"
        )
        assert previously_accepted.status_code == 200
        assert previously_accepted.json()["status"] == "accepted"
        stale_accept = client.post(
            f"/api/reviews/{stale_run['review_bundle_id']}/accept",
            json={},
        )
        assert stale_accept.status_code == 422
        assert client.get(f"/api/projects/{project_id}").json()["canon_version"] == after_accept

        user_id = client.get("/api/auth/me").json()["id"]
        with db_module.SessionLocal() as db:
            indexed_after_reject = db.execute(
                text(
                    "SELECT revision_id FROM chapter_fts "
                    "WHERE project_id = :project_id AND chapter_id = :chapter_id"
                ),
                {"project_id": project_id, "chapter_id": chapter_id},
            ).scalar_one()
            review_audits = db.scalars(
                select(models.AuditLog).where(
                    models.AuditLog.project_id == project_id,
                    models.AuditLog.action.in_(("review.accepted", "review.rejected")),
                )
            ).all()
        assert indexed_after_reject == accepted_revision_id
        assert {entry.action for entry in review_audits} == {
            "review.accepted",
            "review.rejected",
        }
        assert all(entry.actor_user_id == user_id for entry in review_audits)


def test_new_account_requires_explicit_private_provider(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, with_provider=False) as client:
        assert client.get("/api/providers").json() == []
        project = client.post("/api/projects", json={"title": "无模型项目"})
        assert project.status_code == 201
        blocked = client.post(
            f"/api/projects/{project.json()['id']}/generations",
            json={"idempotency_key": "no-provider", "target_word_count": 800},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "provider_required"
        assert client.get(f"/api/projects/{project.json()['id']}/chapters").json() == []
        assert client.get(f"/api/projects/{project.json()['id']}/generations/latest").status_code == 404

        saved = client.post(
            "/api/providers",
            json={
                "name": "我的 Claude",
                "protocol": "anthropic_messages",
                "default_model": "claude-test",
            },
        )
        assert saved.status_code == 201, saved.text
        assert saved.json()["base_url"] == "https://api.anthropic.com/v1"
        assert saved.json()["protocol"] == "anthropic_messages"
        assert saved.json()["api_key_set"] is False
        selected = client.put(f"/api/providers/{saved.json()['id']}/default")
        assert selected.status_code == 200, selected.text
        assert selected.json()["is_default"] is True
