"""HTTP coverage for manually editing assistant proposal drafts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app import db as db_module
from backend.app import models
from backend.app.db import create_engine_for_url, get_db, init_db
from backend.app.main import app
from backend.tests.helpers import authenticate_client


@pytest.fixture()
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'proposal-edit.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    previous_overrides = dict(app.dependency_overrides)

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app, base_url="http://127.0.0.1")
    owner_id = authenticate_client(client, factory, email="proposal-owner@example.test")
    try:
        yield client, factory, owner_id
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        engine.dispose()


def _new_project(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/projects", json={"title": "手动草稿提案"})
    assert response.status_code == 201, response.text
    return response.json()


def _seed_character_proposal(
    factory: Any,
    project_id: str,
    owner_id: str,
    *,
    status: str = "proposed",
    change_set_status: str = "proposed",
    patch: dict[str, Any] | None = None,
) -> tuple[str, int, int, str]:
    with factory() as db:
        project = db.get(models.Project, project_id)
        assert project is not None
        character = models.Character(project_id=project.id, name="林渡", role="旧角色")
        db.add(character)
        db.flush()
        change_set = models.ChangeSet(
            project_id=project.id,
            source_type="assistant",
            status=change_set_status,
            base_memory_epoch=project.memory_epoch,
            changes_json=[
                {
                    "operation": "update_character",
                    "target_type": "character",
                    "target_id": character.id,
                    "patch": patch or {"role": "新角色"},
                }
            ],
            created_by_user_id=owner_id,
        )
        db.add(change_set)
        db.flush()
        proposal = models.Proposal(
            project_id=project.id,
            change_set_id=change_set.id,
            operation="update_character",
            target_type="character",
            target_id=character.id,
            patch_json=patch or {"role": "新角色"},
            base_version=character.version,
            base_memory_epoch=project.memory_epoch,
            status=status,
            reason="手动草稿",
            created_by_user_id=owner_id,
        )
        db.add(proposal)
        db.commit()
        return proposal.id, character.version, project.memory_epoch, character.id


def test_edit_existing_value_then_apply_uses_normal_cas_path(api) -> None:
    client, factory, owner_id = api
    project = _new_project(client)
    proposal_id, base_version, base_epoch, character_id = _seed_character_proposal(
        factory, project["id"], owner_id
    )

    edited = client.patch(
        f"/api/projects/{project['id']}/assistant/proposals/{proposal_id}",
        json={"patches": [{"op": "replace", "path": "role", "value": "灯塔守夜人"}]},
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["status"] == "proposed"
    assert body["patch_json"] == {"role": "灯塔守夜人"}
    assert body["base_version"] == base_version
    assert body["base_memory_epoch"] == base_epoch

    applied = client.post(
        f"/api/projects/{project['id']}/assistant/proposals/{proposal_id}/apply",
        json={"expected_version": base_version, "expected_memory_epoch": base_epoch},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"
    with factory() as db:
        character = db.get(models.Character, character_id)
        assert character is not None
        assert character.role == "灯塔守夜人"
        audit = db.scalar(
            select(models.AuditLog).where(
                models.AuditLog.entity_id == proposal_id,
                models.AuditLog.action == "assistant.proposal_edited",
            )
        )
        assert audit is not None


def test_edit_cannot_add_paths_or_change_authoritative_fields(api) -> None:
    client, factory, owner_id = api
    project = _new_project(client)
    proposal_id, _base_version, _base_epoch, _character_id = _seed_character_proposal(
        factory,
        project["id"],
        owner_id,
        patch={"role": "新角色", "target_id": "server-target"},
    )

    unknown = client.patch(
        f"/api/projects/{project['id']}/assistant/proposals/{proposal_id}",
        json={"patches": [{"op": "replace", "path": "background", "value": "不应扩展"}]},
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "proposal_patch_invalid"

    authority = client.patch(
        f"/api/projects/{project['id']}/assistant/proposals/{proposal_id}",
        json={"patches": [{"op": "replace", "path": "target_id", "value": "other"}]},
    )
    assert authority.status_code == 422
    assert authority.json()["detail"]["code"] == "proposal_patch_invalid"
    with factory() as db:
        proposal = db.get(models.Proposal, proposal_id)
        assert proposal is not None
        assert proposal.patch_json == {"role": "新角色", "target_id": "server-target"}
        assert proposal.status == "proposed"


def test_pending_proposal_is_promoted_only_after_successful_edit(api) -> None:
    client, factory, owner_id = api
    project = _new_project(client)
    proposal_id, _base_version, _base_epoch, _character_id = _seed_character_proposal(
        factory,
        project["id"],
        owner_id,
        status="pending",
        change_set_status="pending",
    )
    edited = client.patch(
        f"/api/projects/{project['id']}/assistant/proposals/{proposal_id}",
        json={"patches": [{"path": "/role", "value": "已确认角色"}]},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "proposed"
    with factory() as db:
        change_set = db.scalar(
            select(models.ChangeSet).where(models.ChangeSet.project_id == project["id"])
        )
        assert change_set is not None
        assert change_set.status == "proposed"
        assert change_set.changes_json[0]["patch"]["role"] == "已确认角色"


def test_edit_rejects_stale_entity_and_tenant_access(api) -> None:
    client, factory, owner_id = api
    project = _new_project(client)
    proposal_id, base_version, base_epoch, character_id = _seed_character_proposal(
        factory, project["id"], owner_id
    )
    with factory() as db:
        character = db.get(models.Character, character_id)
        assert character is not None
        character.role = "其他窗口已改"
        character.version += 1
        db.commit()

    stale = client.patch(
        f"/api/projects/{project['id']}/assistant/proposals/{proposal_id}",
        json={
            "expected_version": base_version,
            "expected_memory_epoch": base_epoch,
            "patches": [{"path": "role", "value": "不应覆盖"}],
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "proposal_conflict"
    with factory() as db:
        proposal = db.get(models.Proposal, proposal_id)
        assert proposal is not None and proposal.status == "conflict"

    other = TestClient(app, base_url="http://127.0.0.1")
    try:
        authenticate_client(other, factory, email="proposal-other@example.test")
        foreign = other.patch(
            f"/api/projects/{project['id']}/assistant/proposals/{proposal_id}",
            json={"patches": [{"path": "role", "value": "跨租户"}]},
        )
        assert foreign.status_code == 404
    finally:
        other.close()


def test_terminal_proposal_cannot_be_edited(api) -> None:
    client, factory, owner_id = api
    project = _new_project(client)
    proposal_id, _base_version, _base_epoch, _character_id = _seed_character_proposal(
        factory, project["id"], owner_id, status="applied", change_set_status="applied"
    )
    response = client.patch(
        f"/api/projects/{project['id']}/assistant/proposals/{proposal_id}",
        json={"patches": [{"path": "role", "value": "不应编辑"}]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "proposal_not_editable"
