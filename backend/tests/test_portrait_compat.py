"""Compatibility coverage for the direct character portrait endpoints."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app import db as db_module
from backend.app import models
from backend.app.db import create_engine_for_url, get_db, init_db
from backend.app.main import app
from backend.app.services import media as media_service
from backend.tests.helpers import authenticate_client, install_fake_provider


@pytest.fixture()
def portrait_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any, str]:
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'portrait.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(media_service, "DATA_DIR", tmp_path / "media")
    install_fake_provider(monkeypatch)
    previous_overrides = dict(app.dependency_overrides)

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app, base_url="http://127.0.0.1")
    owner_id = authenticate_client(client, factory)
    try:
        yield client, factory, owner_id
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        engine.dispose()


def _image_bytes(image_format: str, *, with_exif: bool = False) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (16, 10), (80, 120, 160))
    if with_exif:
        exif = Image.Exif()
        exif[0x010E] = "private portrait metadata"
        image.save(output, format=image_format, exif=exif)
    else:
        image.save(output, format=image_format)
    return output.getvalue()


def test_direct_portrait_lifecycle_is_secure_and_reencoded(portrait_api) -> None:
    client, factory, owner_id = portrait_api
    project_response = client.post("/api/projects", json={"title": "画像兼容", "start_mode": "setup"})
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["id"]
    character_response = client.post(
        f"/api/projects/{project_id}/characters", json={"name": "林渡"}
    )
    assert character_response.status_code == 201, character_response.text
    character_id = character_response.json()["id"]

    uploaded = client.post(
        f"/api/characters/{character_id}/portrait",
        files={"file": ("portrait.jpg", _image_bytes("JPEG", with_exif=True), "image/jpeg")},
        data={"alt": "角色画像"},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    assert asset["mime"] == "image/jpeg"
    assert asset["alt"] == "角色画像"
    assert "storage_key" not in asset and "owner_id" not in asset
    old_asset_id = asset["id"]

    downloaded = client.get(f"/api/characters/{character_id}/portrait")
    assert downloaded.status_code == 200
    with Image.open(io.BytesIO(downloaded.content)) as image:
        assert image.format == "JPEG"
        assert image.getexif() == {}
    metadata = client.get(f"/api/characters/{character_id}/portrait/metadata")
    assert metadata.status_code == 200
    assert metadata.json()["id"] == old_asset_id

    replaced = client.post(
        f"/api/characters/{character_id}/portrait",
        files={"file": ("portrait.webp", _image_bytes("WEBP"), "image/webp")},
    )
    assert replaced.status_code == 201, replaced.text
    new_asset_id = replaced.json()["id"]
    assert new_asset_id != old_asset_id
    assert client.get(f"/api/media/{old_asset_id}").status_code == 404

    invalid = client.post(
        f"/api/characters/{character_id}/portrait",
        files={"file": ("bad.png", b"not-an-image", "image/png")},
    )
    assert invalid.status_code == 422
    oversized = client.post(
        f"/api/characters/{character_id}/portrait",
        files={"file": ("large.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")},
    )
    assert oversized.status_code == 422

    deleted = client.delete(f"/api/characters/{character_id}/portrait")
    assert deleted.status_code == 204
    assert client.get(f"/api/characters/{character_id}/portrait").status_code == 404
    assert client.get(f"/api/characters/{character_id}/portrait/metadata").status_code == 404
    with factory() as db:
        character = db.get(models.Character, character_id)
        assert character is not None and character.image_media_id is None
        assert db.scalar(select(models.MediaAsset).where(models.MediaAsset.id == new_asset_id)) is None
        assert db.scalar(
            select(models.AuditLog).where(
                models.AuditLog.project_id == project_id,
                models.AuditLog.actor_user_id == owner_id,
                models.AuditLog.action == "character.portrait_deleted",
            )
        ) is not None


def test_direct_portrait_does_not_cross_tenants(portrait_api) -> None:
    client, factory, _owner_id = portrait_api
    project_response = client.post("/api/projects", json={"title": "租户画像", "start_mode": "setup"})
    project_id = project_response.json()["id"]
    character_response = client.post(
        f"/api/projects/{project_id}/characters", json={"name": "仅限本人"}
    )
    character_id = character_response.json()["id"]
    other = TestClient(app, base_url="http://127.0.0.1")
    try:
        authenticate_client(other, factory, email="portrait-other@example.test", with_provider=False)
        assert other.get(f"/api/characters/{character_id}/portrait").status_code == 404
        response = other.post(
            f"/api/characters/{character_id}/portrait",
            files={"file": ("portrait.png", _image_bytes("PNG"), "image/png")},
        )
        assert response.status_code == 404
    finally:
        other.close()


def test_replacing_a_shared_portrait_keeps_the_other_card_image(portrait_api) -> None:
    client, _factory, _owner_id = portrait_api
    project = client.post(
        "/api/projects", json={"title": "共享画像", "start_mode": "setup"}
    ).json()
    first = client.post(
        f"/api/projects/{project['id']}/characters", json={"name": "甲"}
    ).json()
    second = client.post(
        f"/api/projects/{project['id']}/characters", json={"name": "乙"}
    ).json()
    shared = client.post(
        f"/api/characters/{first['id']}/portrait",
        files={"file": ("shared.png", _image_bytes("PNG"), "image/png")},
    ).json()
    attached = client.patch(
        f"/api/projects/{project['id']}/characters/{second['id']}",
        json={"image_media_id": shared["id"], "expected_version": second["version"]},
    )
    assert attached.status_code == 200, attached.text

    replaced = client.post(
        f"/api/characters/{first['id']}/portrait",
        files={"file": ("replacement.webp", _image_bytes("WEBP"), "image/webp")},
    )
    assert replaced.status_code == 201, replaced.text
    assert client.get(f"/api/media/{shared['id']}").status_code == 200
    assert client.get(f"/api/characters/{second['id']}/portrait").status_code == 200
