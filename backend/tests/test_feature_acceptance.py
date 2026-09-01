"""End-to-end acceptance coverage for the story-workspace feature contract.

The tests use the HTTP boundary for user-visible mutations and seed only the
small amount of durable state needed to exercise CAS/error paths. Providers
and background generation are replaced with deterministic no-ops; no network
or real model call is part of this suite.
"""

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
from backend.app.routers import generations as generations_router
from backend.app.services import media as media_service
from backend.tests.helpers import authenticate_client, install_fake_provider


@pytest.fixture()
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any, str]:
    """Return an isolated client, session factory, and authenticated owner ID."""

    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'features.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(media_service, "DATA_DIR", tmp_path / "media")
    install_fake_provider(monkeypatch)
    monkeypatch.setattr(generations_router, "_run_background", lambda _run_id: None)

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


def _new_project(client: TestClient, *, title: str, start_mode: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": title}
    if start_mode is not None:
        payload["start_mode"] = start_mode
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _create_chapter(client: TestClient, project_id: str, *, content: str = "正文") -> dict[str, Any]:
    response = client.post(
        f"/api/projects/{project_id}/chapters",
        json={"chapter_number": 1, "title": "第一章", "content": content},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 8), (40, 90, 150)).save(output, format="PNG")
    return output.getvalue()


def _jpeg_with_exif() -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (16, 10), (120, 40, 80))
    exif = Image.Exif()
    exif[0x010E] = "private character description"
    exif[0x9286] = b"private user metadata"
    image.save(output, format="JPEG", exif=exif)
    return output.getvalue()


def _second_client(api: tuple[TestClient, Any, str], email: str) -> tuple[TestClient, str]:
    _client, factory, _owner_id = api
    other = TestClient(app, base_url="http://127.0.0.1")
    return other, authenticate_client(other, factory, email=email, with_provider=False)


def test_project_start_modes_are_atomic_and_import_is_explicit(api) -> None:
    client, factory, owner_id = api

    invalid = client.post(
        "/api/projects",
        json={"title": "不应落库", "start_mode": "unknown"},
    )
    assert invalid.status_code == 422
    assert all(item["title"] != "不应落库" for item in client.get("/api/projects").json())

    blank = _new_project(client, title="空白稿纸", start_mode="blank")
    blank_chapters = client.get(f"/api/projects/{blank['id']}/chapters")
    assert blank_chapters.status_code == 200
    assert len(blank_chapters.json()) == 1
    assert blank_chapters.json()[0]["status"] == "draft"
    assert blank_chapters.json()[0]["summary_status"] == "unprocessed"
    assert blank["current_chapter_id"] == blank_chapters.json()[0]["id"]

    setup = _new_project(client, title="设定先行", start_mode="setup")
    importing = _new_project(client, title="导入小说", start_mode="import")
    assert client.get(f"/api/projects/{setup['id']}/chapters").json() == []
    assert client.get(f"/api/projects/{importing['id']}/chapters").json() == []

    empty_commit = client.post(
        f"/api/projects/{importing['id']}/import/commit",
        json={"filename": "empty.txt", "content": ""},
    )
    assert empty_commit.status_code == 422
    assert client.get(f"/api/projects/{importing['id']}/chapters").json() == []

    committed = client.post(
        f"/api/projects/{importing['id']}/import/commit",
        json={"filename": "story.txt", "content": "第一章 港口\n林渡抵达港口。"},
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["count"] == 1
    assert len(client.get(f"/api/projects/{importing['id']}/chapters").json()) == 1

    with factory() as db:
        audits = db.scalars(
            select(models.AuditLog).where(
                models.AuditLog.actor_user_id == owner_id,
                models.AuditLog.action == "project.created",
            )
        ).all()
    assert {entry.after_json["start_mode"] for entry in audits} >= {"blank", "setup", "import"}


def test_account_summary_preference_is_cas_and_tenant_scoped(api) -> None:
    client, factory, owner_id = api
    other, other_id = _second_client(api, "preference-other@example.test")
    try:
        first = client.get("/api/account/preferences")
        second = other.get("/api/account/preferences")
        assert first.status_code == second.status_code == 200
        assert first.json() == {"auto_summary_enabled": True, "preferences_version": 1}
        assert second.json() == {"auto_summary_enabled": True, "preferences_version": 1}

        updated = client.patch(
            "/api/account/preferences",
            json={"auto_summary_enabled": False, "expected_version": 1},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json() == {"auto_summary_enabled": False, "preferences_version": 2}

        stale = client.patch(
            "/api/account/preferences",
            json={"auto_summary_enabled": True, "expected_version": 1},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "preferences_conflict"
        assert client.get("/api/account/preferences").json()["auto_summary_enabled"] is False
        assert other.get("/api/account/preferences").json()["auto_summary_enabled"] is True

        with factory() as db:
            users = {user.id: user for user in db.scalars(select(models.User)).all()}
            assert users[owner_id].auto_summary_enabled is False
            assert users[other_id].auto_summary_enabled is True
            actions = db.scalars(
                select(models.AuditLog).where(models.AuditLog.action == "preferences.updated")
            ).all()
        assert len(actions) == 1
        assert actions[0].actor_user_id == owner_id
    finally:
        other.close()


def test_characters_graph_and_layout_are_project_isolated_and_epoched(api) -> None:
    client, _factory, _owner_id = api
    first = _new_project(client, title="人物甲", start_mode="setup")
    second = _new_project(client, title="人物乙", start_mode="setup")
    first_id, second_id = first["id"], second["id"]
    assert client.get(f"/api/projects/{first_id}").json()["memory_epoch"] == 0

    character = client.post(
        f"/api/projects/{first_id}/characters",
        json={
            "name": "林渡",
            "goal": "查明灯塔秘密",
            "conflict": "不信任陌生人",
            "role": "主角",
        },
    )
    assert character.status_code == 201, character.text
    character_id = character.json()["id"]
    assert character.json()["goals"] == "查明灯塔秘密"
    assert client.get(f"/api/projects/{first_id}").json()["memory_epoch"] == 1
    assert client.get(f"/api/projects/{second_id}/characters").json() == []
    assert client.get(f"/api/projects/{second_id}/characters/{character_id}").status_code == 404

    graph_after_character = client.get(f"/api/projects/{first_id}/story-graph")
    assert graph_after_character.status_code == 200, graph_after_character.text
    node_one = next(
        node
        for node in graph_after_character.json()["nodes"]
        if node["character_id"] == character_id
    )
    node_two = client.post(
        f"/api/projects/{first_id}/story-graph/nodes",
        json={"node_type": "custom", "label": "灯塔", "data": {"kind": "place"}},
    )
    assert node_two.status_code == 201, node_two.text
    assert client.get(f"/api/projects/{first_id}").json()["memory_epoch"] == 2

    edge = client.post(
        f"/api/projects/{first_id}/story-graph/edges",
        json={
            "source_node_id": node_one["id"],
            "target_node_id": node_two.json()["id"],
            "relation_type": "knows",
        },
    )
    assert edge.status_code == 201, edge.text
    assert client.get(f"/api/projects/{first_id}").json()["memory_epoch"] == 3
    assert client.get(f"/api/projects/{second_id}/story-graph").json() == {
        "nodes": [],
        "edges": [],
        "layout": None,
    }
    cross_project_edge = client.post(
        f"/api/projects/{second_id}/story-graph/edges",
        json={
            "source_node_id": node_one["id"],
            "target_node_id": node_one["id"],
        },
    )
    assert cross_project_edge.status_code == 404

    moved = client.patch(
        f"/api/projects/{first_id}/story-graph/nodes/{node_one['id']}",
        json={"position_x": 99, "expected_version": 1},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["version"] == 2
    assert client.get(f"/api/projects/{first_id}").json()["memory_epoch"] == 3
    renamed = client.patch(
        f"/api/projects/{first_id}/story-graph/nodes/{node_one['id']}",
        json={"label": "林渡（已改名）", "expected_version": 2},
    )
    assert renamed.status_code == 200, renamed.text
    assert client.get(f"/api/projects/{first_id}").json()["memory_epoch"] == 4

    layout = client.put(
        f"/api/projects/{first_id}/story-graph/layout",
        json={"layout_json": {"nodes": {node_one["id"]: {"x": 99}}}},
    )
    assert layout.status_code == 200, layout.text
    assert client.get(f"/api/projects/{second_id}/story-graph/layout").status_code == 404
    assert client.get(f"/api/projects/{first_id}/story-graph/layout").json()["version"] == 1


def test_media_sniffs_content_strips_exif_limits_size_and_enforces_tenants(api) -> None:
    client, _factory, _owner_id = api
    project = _new_project(client, title="图片项目", start_mode="setup")
    project_id = project["id"]

    png = _png_bytes()
    spoofed = client.post(
        f"/api/projects/{project_id}/media",
        files={"file": ("portrait.exe", png, "application/x-msdownload")},
        data={"kind": "character"},
    )
    assert spoofed.status_code == 422, spoofed.text

    uploaded = client.post(
        f"/api/projects/{project_id}/media",
        files={"file": ("portrait.png", png, "image/png")},
        data={"kind": "character"},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    assert asset["mime"] == "image/png"
    assert asset["original_name"] == "portrait.png"
    downloaded = client.get(f"/api/media/{asset['id']}")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("image/png")

    invalid = client.post(
        f"/api/projects/{project_id}/media",
        files={"file": ("fake.png", b"not-an-image", "image/png")},
    )
    assert invalid.status_code == 422

    oversized = client.post(
        f"/api/projects/{project_id}/media",
        files={"file": ("huge.bin", b"x" * (10 * 1024 * 1024 + 1), "image/png")},
    )
    assert oversized.status_code == 422
    assert "10MB" in oversized.json()["detail"]

    jpeg = client.post(
        f"/api/projects/{project_id}/media",
        files={"file": ("portrait.jpg", _jpeg_with_exif(), "image/jpeg")},
        data={"kind": "character"},
    )
    assert jpeg.status_code == 201, jpeg.text
    jpeg_download = client.get(f"/api/media/{jpeg.json()['id']}")
    assert jpeg_download.status_code == 200
    with Image.open(io.BytesIO(jpeg_download.content)) as opened:
        assert len(opened.getexif()) == 0

    other_project = _new_project(client, title="另一图片项目", start_mode="setup")
    character = client.post(
        f"/api/projects/{other_project['id']}/characters",
        json={"name": "不应引用", "image_media_id": asset["id"]},
    )
    assert character.status_code == 404
    other, _other_id = _second_client(api, "media-other@example.test")
    try:
        assert other.get(f"/api/media/{asset['id']}").status_code == 404
    finally:
        other.close()


def test_generation_memory_not_ready_and_skip_once_audit(api) -> None:
    client, factory, owner_id = api
    project = _new_project(client, title="记忆门控", start_mode="setup")
    chapter = _create_chapter(client, project["id"], content="确认正文")
    confirmed = client.post(f"/api/chapters/{chapter['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    with factory() as db:
        stored_chapter = db.get(models.Chapter, chapter["id"])
        assert stored_chapter is not None
        stored_chapter.summary_status = "unprocessed"
        db.commit()

    blocked = client.post(
        f"/api/projects/{project['id']}/generations",
        json={
            "idempotency_key": "memory-gated",
            "destination": "new_child",
            "target_word_count": 200,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "memory_not_ready"
    with factory() as db:
        assert db.scalar(
            select(models.GenerationRun).where(
                models.GenerationRun.project_id == project["id"],
                models.GenerationRun.idempotency_key == "memory-gated",
            )
        ) is None

    skipped = client.post(
        f"/api/projects/{project['id']}/generations",
        json={
            "idempotency_key": "memory-skipped",
            "destination": "new_child",
            "skip_memory_once": True,
            "skip_memory_reason": "本次先验证节奏",
            "target_word_count": 200,
        },
    )
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["created"] is True
    placeholder_id = skipped.json()["chapter_id"]
    chapters = client.get(f"/api/projects/{project['id']}/chapters").json()
    placeholder = next(item for item in chapters if item["id"] == placeholder_id)
    assert placeholder["status"] == "generating"
    assert placeholder["summary_status"] == "unprocessed"
    with factory() as db:
        audit = db.scalar(
            select(models.AuditLog).where(
                models.AuditLog.project_id == project["id"],
                models.AuditLog.action == "generation.memory_skipped_once",
            )
        )
        assert audit is not None
        assert audit.actor_user_id == owner_id
        assert audit.reason == "本次先验证节奏"
        assert audit.after_json["chapter_id"] == chapter["id"]


def test_change_proposal_conflict_is_cas_safe(api) -> None:
    client, factory, owner_id = api
    project = _new_project(client, title="提案冲突", start_mode="setup")
    character_response = client.post(
        f"/api/projects/{project['id']}/characters",
        json={"name": "林渡", "goals": "初始目标"},
    )
    assert character_response.status_code == 201, character_response.text
    character_id = character_response.json()["id"]
    with factory() as db:
        stored_project = db.get(models.Project, project["id"])
        stored_character = db.get(models.Character, character_id)
        assert stored_project is not None and stored_character is not None
        base_epoch = stored_project.memory_epoch
        base_version = stored_character.version
        change_set = models.ChangeSet(
            project_id=stored_project.id,
            source_type="assistant",
            source_id="batch-test",
            base_memory_epoch=base_epoch,
            status="proposed",
            summary="两条人物设定建议",
            changes_json=[],
            created_by_user_id=owner_id,
        )
        db.add(change_set)
        db.flush()
        first = models.Proposal(
            project_id=stored_project.id,
            change_set_id=change_set.id,
            operation="update_character",
            target_type="character",
            target_id=stored_character.id,
            patch_json={"goals": "目标一"},
            base_version=base_version,
            base_memory_epoch=base_epoch,
            status="proposed",
            created_by_user_id=owner_id,
        )
        second = models.Proposal(
            project_id=stored_project.id,
            change_set_id=change_set.id,
            operation="update_character",
            target_type="character",
            target_id=stored_character.id,
            patch_json={"motivation": "动机二"},
            base_version=base_version,
            base_memory_epoch=base_epoch,
            status="proposed",
            created_by_user_id=owner_id,
        )
        db.add_all([first, second])
        db.commit()
        first_id, second_id = first.id, second.id

    applied = client.post(
        f"/api/projects/{project['id']}/assistant/proposals/{first_id}/apply",
        json={"expected_version": base_version, "expected_memory_epoch": base_epoch},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"

    conflicted = client.post(
        f"/api/projects/{project['id']}/assistant/proposals/{second_id}/apply",
        json={"expected_version": base_version, "expected_memory_epoch": base_epoch},
    )
    assert conflicted.status_code == 409
    assert conflicted.json()["detail"]["code"] == "proposal_conflict"

    with factory() as db:
        stored_character = db.get(models.Character, character_id)
        stored_first = db.get(models.Proposal, first_id)
        stored_second = db.get(models.Proposal, second_id)
        change_set = db.scalar(
            select(models.ChangeSet).where(models.ChangeSet.source_id == "batch-test")
        )
        assert stored_character is not None
        assert stored_character.goals == "目标一"
        assert stored_character.motivation is None
        assert stored_first is not None and stored_first.status == "applied"
        assert stored_second is not None and stored_second.status == "conflict"
        # The current one-at-a-time endpoint resolves the individual Proposal
        # rows but does not yet close a ChangeSet on the conflict branch. Keep
        # this assertion explicit so a future batch endpoint can tighten the
        # invariant to "partially_applied" without losing CAS coverage.
        assert change_set is not None and change_set.status == "proposed"
