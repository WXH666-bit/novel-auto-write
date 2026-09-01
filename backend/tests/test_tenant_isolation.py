"""Every ID-addressable story resource must be hidden across tenants."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.app import db as db_module
from backend.app.db import create_engine_for_url, get_db, init_db
from backend.app.main import app
from backend.app.routers import imports as imports_router
from backend.app.services import exports as export_service
from backend.tests.helpers import authenticate_client, install_fake_provider


def test_second_user_gets_404_for_every_first_user_resource(tmp_path, monkeypatch):
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'tenants.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(imports_router, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(export_service, "DATA_DIR", tmp_path)
    install_fake_provider(monkeypatch)

    def override_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    first = TestClient(app, base_url="http://127.0.0.1")
    second = TestClient(app, base_url="http://127.0.0.1")
    authenticate_client(first, factory, email="first@example.test")
    authenticate_client(second, factory, email="second@example.test")

    with first, second:
        project_response = first.post("/api/projects", json={"title": "甲的私有长篇"})
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]
        chapter_response = first.post(
            f"/api/projects/{project_id}/chapters",
            json={"chapter_number": 1, "title": "第一章", "content": "只属于甲的正文。"},
        )
        assert chapter_response.status_code == 201
        chapter_id = chapter_response.json()["id"]
        revision_id = chapter_response.json()["current_revision"]["id"]
        canon_response = first.post(
            f"/api/projects/{project_id}/canon",
            json={"category": "人物", "key": "林渡.身份", "value": "船医"},
        )
        assert canon_response.status_code == 201
        canon_id = canon_response.json()["id"]
        generation_response = first.post(
            f"/api/projects/{project_id}/generations",
            json={
                "chapter_id": chapter_id,
                "idempotency_key": "tenant-bound-run",
                "target_word_count": 500,
            },
        )
        assert generation_response.status_code == 200, generation_response.text
        run_id = generation_response.json()["id"]
        review_id = first.get(f"/api/generations/{run_id}").json()["review_bundle_id"]
        assert review_id
        provider_id = first.get("/api/providers").json()[0]["id"]

        project_cases = [
            ("get", f"/api/projects/{project_id}", None),
            ("patch", f"/api/projects/{project_id}", {"title": "偷改"}),
            ("delete", f"/api/projects/{project_id}", None),
            ("get", f"/api/projects/{project_id}/story-map", None),
            ("get", f"/api/projects/{project_id}/chapters", None),
            ("post", f"/api/projects/{project_id}/chapters", {"title": "越权章节"}),
            ("get", f"/api/projects/{project_id}/canon", None),
            ("post", f"/api/projects/{project_id}/canon", {"key": "越权", "value": "否"}),
            ("get", f"/api/projects/{project_id}/reviews", None),
            ("get", f"/api/projects/{project_id}/generations/latest", None),
            ("get", f"/api/projects/{project_id}/export", None),
            ("post", f"/api/projects/{project_id}/memory/rebuild", None),
            (
                "post",
                f"/api/projects/{project_id}/imports/preview-text",
                {"filename": "stolen.txt", "content": "第一章\n越权"},
            ),
            (
                "post",
                f"/api/projects/{project_id}/import",
                {"filename": "stolen.txt", "content": "第一章\n越权"},
            ),
        ]
        for method, url, payload in project_cases:
            response = second.request(method, url, json=payload)
            assert response.status_code == 404, (method, url, response.text)

        child_cases = [
            ("get", f"/api/chapters/{chapter_id}", None),
            ("patch", f"/api/chapters/{chapter_id}", {"title": "偷改"}),
            ("get", f"/api/chapters/{chapter_id}/revisions", None),
            ("get", f"/api/chapters/{chapter_id}/revisions/{revision_id}", None),
            ("post", f"/api/chapters/{chapter_id}/revisions", {"content": "越权修订"}),
            ("post", f"/api/chapters/{chapter_id}/confirm", None),
            ("get", f"/api/canon/{canon_id}", None),
            ("patch", f"/api/canon/{canon_id}", {"note": "偷改"}),
            ("post", f"/api/canon/{canon_id}/confirm", {}),
            ("post", f"/api/projects/{project_id}/canon/{canon_id}/confirm", {}),
            ("post", f"/api/canon/{canon_id}/needs-review", {}),
            ("get", f"/api/generations/{run_id}", None),
            ("get", f"/api/generations/{run_id}/events", None),
            ("post", f"/api/generations/{run_id}/retry", None),
            ("get", f"/api/reviews/{review_id}", None),
            ("post", f"/api/reviews/{review_id}/draft", {"content": "偷改"}),
            ("post", f"/api/reviews/{review_id}/reaudit", {}),
            ("post", f"/api/reviews/{review_id}/accept", {}),
            ("post", f"/api/reviews/{review_id}/reject", {"reason": "偷拒绝"}),
            ("post", f"/api/reviews/{review_id}/decision", {"action": "accept"}),
            ("get", f"/api/providers/{provider_id}", None),
            ("put", f"/api/providers/{provider_id}/default", None),
            ("post", f"/api/providers/{provider_id}/key", {"api_key": "stolen"}),
            ("delete", f"/api/providers/{provider_id}/key", None),
            ("post", f"/api/providers/{provider_id}/test", None),
            ("delete", f"/api/providers/{provider_id}", None),
        ]
        for method, url, payload in child_cases:
            response = second.request(method, url, json=payload)
            assert response.status_code == 404, (method, url, response.text)

        import_response = second.post(
            f"/api/projects/{project_id}/imports/preview",
            files={"file": ("stolen.txt", "第一章\n越权".encode(), "text/plain")},
        )
        assert import_response.status_code == 404
        import_commit = second.post(
            f"/api/projects/{project_id}/imports/commit",
            json={"filename": "stolen.txt", "content": "第一章\n越权"},
        )
        assert import_commit.status_code == 404
        restore_response = second.post(
            "/api/projects/restore",
            params={"project_id": project_id},
            files={"file": ("invalid.zip", b"not-a-zip", "application/zip")},
        )
        assert restore_response.status_code == 404

        second_project = second.post("/api/projects", json={"title": "乙的项目"})
        assert second_project.status_code == 201
        foreign_provider = second.post(
            f"/api/projects/{second_project.json()['id']}/generations",
            json={
                "provider_id": provider_id,
                "idempotency_key": "foreign-provider",
            },
        )
        assert foreign_provider.status_code == 404
        assert second.get(
            f"/api/projects/{second_project.json()['id']}/chapters"
        ).json() == []
        assert second.delete(f"/api/projects/{second_project.json()['id']}").status_code == 204

        assert second.get("/api/projects").json() == []
        assert len(first.get("/api/projects").json()) == 1

    app.dependency_overrides.pop(get_db, None)
    engine.dispose()
