"""Core regression tests for append-only revisions and canon gating."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.app import db as db_module
from backend.app.db import create_engine_for_url, get_db, init_db
from backend.app.main import app


def _client(tmp_path, monkeypatch) -> TestClient:
    test_engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}")
    init_db(test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    testing_session = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_module, "SessionLocal", testing_session)

    def override_db():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _new_project(client: TestClient) -> str:
    response = client.post("/api/projects", json={"title": "测试长篇"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_chapter_revisions_are_append_only(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        project_id = _new_project(client)
        chapter = client.post(
            f"/api/projects/{project_id}/chapters",
            json={"chapter_number": 1, "title": "第一章", "content": "旧正文"},
        )
        assert chapter.status_code == 201, chapter.text
        chapter_id = chapter.json()["id"]

        second = client.post(
            f"/api/chapters/{chapter_id}/revisions",
            json={"content": "新正文", "source_type": "manual"},
        )
        assert second.status_code == 201, second.text
        revisions = client.get(f"/api/chapters/{chapter_id}/revisions").json()
        assert [item["revision_number"] for item in revisions] == [1, 2]
        assert revisions[0]["content"] == "旧正文"
        assert revisions[0]["content_hash"] != revisions[1]["content_hash"]
        assert (
            client.get(f"/api/chapters/{chapter_id}").json()["current_revision_id"]
            == second.json()["id"]
        )


def test_confirming_canon_advances_project_version_once(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        project_id = _new_project(client)
        created = client.post(
            f"/api/projects/{project_id}/canon",
            json={"category": "人物", "key": "林渡.身份", "value": "船医", "is_hard": True},
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]
        assert client.get(f"/api/projects/{project_id}").json()["canon_version"] == 0

        confirmed = client.post(f"/api/canon/{item_id}/confirm", json={})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "confirmed"
        assert confirmed.json()["canon_version"] == 1
        replay = client.post(f"/api/canon/{item_id}/confirm", json={})
        assert replay.status_code == 200
        assert client.get(f"/api/projects/{project_id}").json()["canon_version"] == 1


def test_editing_confirmed_old_chapter_invalidates_downstream_memory(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        project_id = _new_project(client)
        first = client.post(
            f"/api/projects/{project_id}/chapters",
            json={"chapter_number": 1, "sort_order": 1, "title": "第一章", "content": "初见"},
        ).json()
        second = client.post(
            f"/api/projects/{project_id}/chapters",
            json={"chapter_number": 2, "sort_order": 2, "title": "第二章", "content": "后续"},
        ).json()
        first_id = first["id"]
        client.post(f"/api/chapters/{first_id}/confirm")
        canon = client.post(
            f"/api/projects/{project_id}/canon",
            json={
                "category": "事件",
                "key": "初见地点",
                "value": "渡口",
                "source_chapter_id": first_id,
            },
        ).json()
        client.post(f"/api/canon/{canon['id']}/confirm", json={})

        revised = client.post(
            f"/api/chapters/{first_id}/revisions",
            json={"content": "改写后的初见"},
        )
        assert revised.status_code == 201, revised.text

        project = client.get(f"/api/projects/{project_id}").json()
        downstream = client.get(f"/api/chapters/{second['id']}").json()
        invalidated_canon = client.get(f"/api/canon/{canon['id']}").json()
        assert project["needs_rebuild"] is True
        assert downstream["summary_status"] == "needs_review"
        assert invalidated_canon["status"] == "needs_review"
        assert client.get(f"/api/chapters/{first_id}").json()["status"] == "needs_review"
