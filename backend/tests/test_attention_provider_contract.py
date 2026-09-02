"""SQLite coverage for the actionable project queue and Provider flags."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
def contract_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any, str]:
    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'contract.sqlite3').as_posix()}")
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
    owner_id = authenticate_client(client, factory, with_provider=False)
    try:
        yield client, factory, owner_id
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        engine.dispose()


def _new_project(client: TestClient, title: str) -> dict[str, Any]:
    response = client.post("/api/projects", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


def test_project_attention_is_actionable_ordered_and_deduplicated(contract_api) -> None:
    client, factory, owner_id = contract_api
    project = _new_project(client, "注意事项")
    project_id = project["id"]
    now = datetime.now(UTC)
    with factory() as db:
        chapter = models.Chapter(
            project_id=project_id,
            chapter_number=1,
            title="第一章",
            status="draft",
        )
        db.add(chapter)
        db.flush()
        pending = models.ReviewBundle(
            project_id=project_id,
            chapter_id=chapter.id,
            status="pending",
            created_at=now - timedelta(minutes=4),
        )
        recheck = models.ReviewBundle(
            project_id=project_id,
            chapter_id=chapter.id,
            status="needs_review",
            created_at=now - timedelta(minutes=3),
        )
        handled = models.ReviewBundle(
            project_id=project_id,
            chapter_id=chapter.id,
            status="accepted",
            created_at=now - timedelta(minutes=2),
        )
        stale = models.ReviewBundle(
            project_id=project_id,
            chapter_id=chapter.id,
            status="stale",
            created_at=now - timedelta(minutes=1),
        )
        change_set = models.ChangeSet(
            project_id=project_id,
            source_type="assistant",
            status="proposed",
            summary="待处理提案",
        )
        proposal = models.Proposal(
            project_id=project_id,
            change_set=change_set,
            operation="update_character",
            target_type="character",
            status="proposed",
            reason="补充动机",
            created_at=now - timedelta(minutes=1),
        )
        failed_run = models.GenerationRun(
            project_id=project_id,
            chapter_id=chapter.id,
            stage="writing",
            status="needs_retry",
            idempotency_key="attention-run",
            started_at=now - timedelta(minutes=6),
        )
        db.add_all([pending, recheck, handled, stale, change_set, proposal, failed_run])
        db.flush()
        failed_job = models.Job(
            project_id=project_id,
            chapter_id=chapter.id,
            idempotency_key="attention-job",
            resource_id=failed_run.id,
            state="failed",
            last_error="sqlalchemy.exc.OperationalError: QueuePool limit reached",
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=5),
        )
        orphan_job = models.Job(
            project_id=project_id,
            idempotency_key="attention-orphan-job",
            kind="memory",
            state="failed",
            last_error="worker stopped before creating a public run",
            created_at=now - timedelta(minutes=7),
            updated_at=now - timedelta(minutes=7),
        )
        db.add_all([failed_job, orphan_job])
        db.flush()
        orphan_job_id = orphan_job.id
        failed_run.job_id = failed_job.id
        db.commit()

    response = client.get(f"/api/projects/{project_id}/attention")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["reviews"] == 1
    assert payload["rechecks"] == 1
    assert payload["proposals"] == 1
    assert payload["retries"] == 1
    assert payload["total"] == 4
    assert [item["kind"] for item in payload["items"]] == [
        "proposal",
        "recheck",
        "review",
        "retry",
    ]
    assert sum(item["id"] in {handled.id, stale.id} for item in payload["items"]) == 0
    assert sum(item["run_id"] == failed_run.id for item in payload["items"]) == 1
    assert all(item["id"] != orphan_job_id for item in payload["items"])
    retry_item = next(item for item in payload["items"] if item["run_id"] == failed_run.id)
    assert retry_item["detail"] == "任务未完成，可以重试"
    assert retry_item["task_type"] == "generation"
    assert retry_item["job_id"] == failed_job.id
    proposal_item = next(item for item in payload["items"] if item["kind"] == "proposal")
    assert proposal_item["task_type"] == "assistant"
    assert proposal_item["target_type"] == "character"

    foreign_client = TestClient(app, base_url="http://127.0.0.1")
    try:
        authenticate_client(foreign_client, factory, email="attention-other@example.test", with_provider=False)
        assert foreign_client.get(f"/api/projects/{project_id}/attention").status_code == 404
    finally:
        foreign_client.close()

    with factory() as db:
        assert db.scalar(select(models.Project.owner_id).where(models.Project.id == project_id)) == owner_id


def test_analysis_proposal_is_not_disguised_as_agent_conversation(contract_api) -> None:
    client, factory, _owner_id = contract_api
    project = _new_project(client, "自动分析提案")
    with factory() as db:
        change_set = models.ChangeSet(
            project_id=project["id"],
            source_type="analysis",
            source_id="memory-stage-id",
            status="proposed",
            summary="章节分析候选",
        )
        proposal = models.Proposal(
            project_id=project["id"],
            change_set=change_set,
            operation="create_character",
            target_type="character",
            status="proposed",
            reason="从正文识别人物",
        )
        db.add_all([change_set, proposal])
        db.commit()
        proposal_id = proposal.id

    response = client.get(f"/api/projects/{project['id']}/attention")
    assert response.status_code == 200, response.text
    item = next(row for row in response.json()["items"] if row["id"] == proposal_id)
    assert item["kind"] == "proposal"
    assert item["task_type"] == "memory"
    assert item["conversation_id"] is None
    assert item["run_id"] is None


def test_provider_capabilities_are_canonical_and_patch_reads_back(contract_api) -> None:
    client, factory, _owner_id = contract_api
    created = client.post(
        "/api/providers",
        json={
            "name": "视觉兼容",
            "base_url": "http://127.0.0.1:9999/v1",
            "default_model": "test-model",
            "capabilities": {
                "image_input": "true",
                "supports_vision": "true",
                "multimodal": "true",
                "json_schema": True,
            },
        },
    )
    assert created.status_code == 201, created.text
    provider_id = created.json()["id"]
    assert created.json()["capabilities"] == {"json_schema": True, "vision": True}

    patched = client.patch(
        f"/api/providers/{provider_id}",
        json={"capabilities": {"vision": False, "image_input": True, "custom": "kept"}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["capabilities"] == {"vision": False, "custom": "kept"}

    # The update response is backed by a committed row, and the next request
    # (a fresh dependency/session) must observe exactly the same canonical
    # object rather than a stale ORM snapshot.
    read_back = client.get(f"/api/providers/{provider_id}")
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()["capabilities"] == {"vision": False, "custom": "kept"}

    with factory() as db:
        profile = db.get(models.ProviderProfile, provider_id)
        assert profile is not None
        assert profile.capabilities == {"vision": False, "custom": "kept"}

    invalid = client.patch(
        f"/api/providers/{provider_id}",
        json={"capabilities": {"vision": "not-a-boolean"}},
    )
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    ("legacy_key", "legacy_value", "expected"),
    [
        ("image_input", "true", True),
        ("supports_vision", "true", True),
        ("multimodal", "true", True),
        ("image_input", "false", False),
        ("supports_vision", "false", False),
        ("multimodal", "false", False),
    ],
)
def test_each_legacy_vision_alias_is_strictly_canonical(
    contract_api, legacy_key: str, legacy_value: str, expected: bool
) -> None:
    client, _factory, _owner_id = contract_api
    response = client.post(
        "/api/providers",
        json={
            "name": f"兼容别名 {legacy_key} {legacy_value}",
            "base_url": "http://127.0.0.1:9999/v1",
            "default_model": "test-model",
            "capabilities": {legacy_key: legacy_value, "json_schema": True},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["capabilities"] == {
        "json_schema": True,
        "vision": expected,
    }
