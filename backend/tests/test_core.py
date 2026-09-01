"""Core regression tests for append-only revisions and canon gating."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.app import db as db_module
from backend.app import models
from backend.app.db import create_engine_for_url, get_db, init_db
from backend.app.main import app
from backend.app.services import storage
from backend.tests.helpers import authenticate_client


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
    client = TestClient(app, base_url="http://127.0.0.1")
    authenticate_client(client, testing_session, with_provider=False)
    return client


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


def test_confirming_chapter_advances_memory_epoch_once(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        project_id = _new_project(client)
        chapter = client.post(
            f"/api/projects/{project_id}/chapters",
            json={"chapter_number": 1, "title": "第一章", "content": "人工定稿"},
        )
        assert chapter.status_code == 201, chapter.text
        chapter_id = chapter.json()["id"]

        first = client.post(f"/api/chapters/{chapter_id}/confirm")
        assert first.status_code == 200, first.text
        assert client.get(f"/api/projects/{project_id}").json()["memory_epoch"] == 1
        replay = client.post(f"/api/chapters/{chapter_id}/confirm")
        assert replay.status_code == 200, replay.text
        assert client.get(f"/api/projects/{project_id}").json()["memory_epoch"] == 1


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


def test_tenant_storage_deletion_can_restore_or_finalize(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    upload = tmp_path / "uploads" / "user-1" / "project-1"
    legacy_assets = tmp_path / "assets" / "user-1" / "project-1"
    legacy_character_assets = tmp_path / "character_assets" / "user-1" / "project-1"
    legacy_media = tmp_path / "media" / "user-1" / "project-1"
    backup = tmp_path / "backups" / "user-1" / "project-1"
    upload.mkdir(parents=True)
    legacy_assets.mkdir(parents=True)
    legacy_character_assets.mkdir(parents=True)
    legacy_media.mkdir(parents=True)
    backup.mkdir(parents=True)
    (upload / "source.bin").write_bytes(b"story")
    (legacy_assets / "portrait.png").write_bytes(b"legacy image")
    (legacy_character_assets / "portrait-2.png").write_bytes(b"legacy image 2")
    (legacy_media / "reference.png").write_bytes(b"legacy image 3")
    (backup / "snapshot.zip").write_bytes(b"backup")

    staged = storage.stage_storage_deletion(owner_id="user-1", project_id="project-1")
    assert not upload.exists()
    assert not legacy_assets.exists()
    assert not legacy_character_assets.exists()
    assert not legacy_media.exists()
    assert not backup.exists()
    staged.restore()
    assert (upload / "source.bin").read_bytes() == b"story"
    assert (legacy_assets / "portrait.png").read_bytes() == b"legacy image"
    assert (legacy_character_assets / "portrait-2.png").read_bytes() == b"legacy image 2"
    assert (legacy_media / "reference.png").read_bytes() == b"legacy image 3"
    assert (backup / "snapshot.zip").read_bytes() == b"backup"

    staged = storage.stage_storage_deletion(owner_id="user-1", project_id="project-1")
    quarantine_root = staged.root
    staged.finalize()
    assert quarantine_root is not None and not quarantine_root.exists()
    assert not upload.exists()
    assert not legacy_assets.exists()
    assert not legacy_character_assets.exists()
    assert not legacy_media.exists()
    assert not backup.exists()


def test_flat_legacy_import_is_relocated_to_tenant_path(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'storage.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    raw = b"legacy source"
    source_hash = hashlib.sha256(raw).hexdigest()
    flat = tmp_path / "uploads" / f"{source_hash}.source"
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_bytes(raw)
    with factory() as db:
        user = models.User(
            email="storage@example.test",
            email_normalized="storage@example.test",
            password_hash="test",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        project = models.Project(owner_id=user.id, name="旧导入")
        db.add(project)
        db.flush()
        imported = models.ImportSource(
            project_id=project.id,
            filename="legacy.txt",
            source_hash=source_hash,
            stored_name=f"{source_hash}.source",
            byte_size=len(raw),
        )
        db.add(imported)
        db.commit()

        migration = storage.migrate_import_storage(db)
        db.commit()
        migration.finalize()
        expected_name = f"{user.id}/{project.id}/{source_hash}.source"
        assert imported.stored_name == expected_name
        assert (tmp_path / "uploads" / expected_name).read_bytes() == raw
        assert not flat.exists()
    engine.dispose()


def test_import_storage_migration_rolls_back_copies_when_later_source_fails(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'storage-failure.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    valid_raw = b"first valid legacy source"
    invalid_declared_raw = b"second expected source"
    valid_hash = hashlib.sha256(valid_raw).hexdigest()
    invalid_hash = hashlib.sha256(invalid_declared_raw).hexdigest()
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    valid_flat = upload_root / f"{valid_hash}.source"
    invalid_flat = upload_root / f"{invalid_hash}.source"
    valid_flat.write_bytes(valid_raw)
    invalid_flat.write_bytes(b"corrupted second source")

    with factory() as db:
        user = models.User(
            email="storage-rollback@example.test",
            email_normalized="storage-rollback@example.test",
            password_hash="test",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        project = models.Project(owner_id=user.id, name="批量旧导入")
        db.add(project)
        db.flush()
        created_at = models.utcnow()
        db.add_all(
            [
                models.ImportSource(
                    id="00000000-0000-0000-0000-000000000001",
                    project_id=project.id,
                    filename="first.txt",
                    source_hash=valid_hash,
                    stored_name=f"{valid_hash}.source",
                    byte_size=len(valid_raw),
                    created_at=created_at,
                ),
                models.ImportSource(
                    id="00000000-0000-0000-0000-000000000002",
                    project_id=project.id,
                    filename="second.txt",
                    source_hash=invalid_hash,
                    stored_name=f"{invalid_hash}.source",
                    byte_size=len(invalid_declared_raw),
                    created_at=created_at,
                ),
            ]
        )
        db.commit()

        try:
            storage.migrate_import_storage(db)
            raise AssertionError("损坏的第二条导入应中止迁移")
        except ValueError as exc:
            assert "哈希不一致" in str(exc)
        expected = upload_root / user.id / project.id / f"{valid_hash}.source"
        assert not expected.exists()
        assert valid_flat.read_bytes() == valid_raw
        assert invalid_flat.read_bytes() == b"corrupted second source"
    engine.dispose()
