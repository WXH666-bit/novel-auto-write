"""End-to-end API checks for the user-visible writing workflow."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.app import db as db_module
from backend.app.db import create_engine_for_url, get_db, init_db
from backend.app.main import app
from backend.app.routers import imports as imports_router
from backend.app.services import exports as export_service


def _client(tmp_path, monkeypatch) -> TestClient:
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
    return TestClient(app)


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
    assert snapshot.json()["status"] == "awaiting_review"
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
        accepted = client.post(
            f"/api/reviews/{accepted_run['review_bundle_id']}/accept",
            json={},
        )
        assert accepted.status_code == 200, accepted.text
        after_accept = client.get(f"/api/projects/{project_id}").json()["canon_version"]
        assert after_accept == before_accept + 1

        rejected_run = _generate(client, project_id, chapter_id, "api-reject-1")
        rejected = client.post(
            f"/api/reviews/{rejected_run['review_bundle_id']}/reject",
            json={"reason": "本章节奏不符合预期"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        assert client.get(f"/api/projects/{project_id}").json()["canon_version"] == after_accept


def test_default_demo_provider_round_trip(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        saved = client.put(
            "/api/providers/default",
            json={
                "name": "本地演示模型",
                "base_url": "http://127.0.0.1:1234/v1",
                "protocol": "demo",
                "default_model": "demo-writer",
                "model_roles": {"writer": "demo-writer"},
                "context_length": 8192,
                "timeout_ms": 30000,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["default_model"] == "demo-writer"
        assert saved.json()["api_key_set"] is False

        tested = client.post(
            "/api/providers/test",
            json={
                "name": "本地演示模型",
                "base_url": "http://127.0.0.1:1234/v1",
                "protocol": "demo",
                "default_model": "demo-writer",
            },
        )
        assert tested.status_code == 200, tested.text
        assert tested.json()["ok"] is True
        assert tested.json()["demo"] is True
