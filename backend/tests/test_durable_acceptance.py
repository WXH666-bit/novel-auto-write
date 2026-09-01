"""Durable task, memory checkpoint, and assistant acceptance coverage.

These tests deliberately exercise the database/service boundaries with fake
providers.  They are intentionally independent from a live model endpoint so
that retries, resume semantics, and optimistic-concurrency behaviour stay
deterministic in CI.
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

from backend.app import db as db_module
from backend.app import models
from backend.app.db import Base, create_engine_for_url, get_db, init_db, run_migrations
from backend.app.main import app
from backend.app.routers import assistant as assistant_router
from backend.app.services import assistant as assistant_service
from backend.app.services import generation as generation_service
from backend.app.services import memory as memory_service
from backend.app.services.memory import create_memory_run, execute_memory_run
from backend.app.services.providers import ProviderError, ProviderResponse
from backend.app.services.tasks import DurableTaskRunner
from backend.tests.helpers import authenticate_client, install_fake_provider, seed_tenant


@pytest.fixture()
def store(tmp_path: Path):
    """A model-complete SQLite store for service-level task tests."""

    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'store.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        engine.dispose()


@pytest.fixture()
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An isolated authenticated HTTP boundary for assistant tests."""

    engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'assistant.sqlite3').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_fake_provider(monkeypatch)
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
    owner_id = authenticate_client(client, factory)
    try:
        yield client, factory, owner_id
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        engine.dispose()


def _project(db: Any, owner_id: str, name: str = "任务验收项目") -> models.Project:
    project = models.Project(owner_id=owner_id, name=name)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _confirmed_chapter(
    db: Any,
    project: models.Project,
    content: str,
    *,
    title: str = "第一章",
) -> tuple[models.Chapter, models.ChapterRevision]:
    chapter = models.Chapter(
        project_id=project.id,
        volume_number=1,
        chapter_number=1,
        sort_order=0,
        title=title,
        status="confirmed",
        summary_status="unprocessed",
        source_type="manual",
    )
    db.add(chapter)
    db.flush()
    revision = models.ChapterRevision(
        chapter_id=chapter.id,
        revision_number=1,
        content=content,
        content_hash=models.ChapterRevision.hash_content(content),
        source_type="manual",
    )
    db.add(revision)
    db.flush()
    chapter.current_revision_id = revision.id
    chapter.accepted_revision_id = revision.id
    chapter.confirmed_at = models.utcnow()
    db.commit()
    db.refresh(chapter)
    db.refresh(revision)
    return chapter, revision


def _wait_until(predicate: Any, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_generation_sse_replays_persisted_events_after_last_event_id(
    store: tuple[Any, Any],
) -> None:
    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id)
        run = models.GenerationRun(
            project_id=project.id,
            stage="queued",
            status="queued",
            idempotency_key="sse-replay",
            input_snapshot={},
            model_params={},
            context_snapshot={},
            provider_snapshot={},
        )
        db.add(run)
        db.commit()
        generation_service._set_stage(db, run, "planning")
        generation_service._set_stage(db, run, "drafting")
        generation_service._set_stage(db, run, "failed", status="failed")
        rows = db.scalars(
            select(models.GenerationArtifact)
            .where(
                models.GenerationArtifact.generation_run_id == run.id,
                models.GenerationArtifact.artifact_type == "event",
            )
            .order_by(models.GenerationArtifact.created_at, models.GenerationArtifact.id)
        ).all()
        run_id = run.id
        first_id = rows[0].id
        later_ids = [row.id for row in rows[1:]]

    stream = "".join(
        generation_service.sse_events(
            factory,
            run_id,
            after_event_id=first_id,
            poll_seconds=0,
            max_seconds=0.1,
        )
    )
    assert f"id: {first_id}\n" not in stream
    assert all(f"id: {event_id}\n" in stream for event_id in later_ids)
    assert '"status": "failed"' in stream


def test_durable_runner_serializes_one_project_and_dispatches_each_job_once(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id)
        first = models.Job(
            project_id=project.id,
            idempotency_key="runner-first",
            kind="generation",
            state="queued",
            created_at=models.utcnow() - timedelta(seconds=2),
        )
        second = models.Job(
            project_id=project.id,
            idempotency_key="runner-second",
            kind="generation",
            state="queued",
            created_at=models.utcnow() - timedelta(seconds=1),
        )
        db.add_all([first, second])
        db.commit()
        first_id, second_id = first.id, second.id

    dispatched: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def fake_dispatch(session: Any, job_id: str) -> None:
        job = session.get(models.Job, job_id)
        assert job is not None
        dispatched.append(job_id)
        job.state = "completed"
        job.current_stage = "completed"
        session.commit()
        started.set()
        release.wait(timeout=3)

    monkeypatch.setattr(DurableTaskRunner, "_dispatch", staticmethod(fake_dispatch))
    runner = DurableTaskRunner(factory, workers=2)
    try:
        assert runner.run_once() == 1
        assert started.wait(timeout=2)

        # Even though a second worker is available, the project slot is held
        # until the first dispatch returns.
        assert runner.run_once() == 0
        assert dispatched == [first_id]

        release.set()
        assert _wait_until(lambda: not runner._running_jobs)
        assert runner.run_once() == 1
        assert _wait_until(lambda: not runner._running_jobs)
    finally:
        release.set()
        runner.stop()

    assert dispatched == [first_id, second_id]
    with factory() as db:
        states = {
            row.id: row.state
            for row in db.scalars(select(models.Job).where(models.Job.id.in_([first_id, second_id]))).all()
        }
    assert states == {first_id: "completed", second_id: "completed"}


def test_durable_runner_recovers_expired_lease_and_linked_memory_run(store: tuple[Any, Any]) -> None:
    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "租约恢复")
        run = models.MemoryBuildRun(
            project_id=project.id,
            scope="project",
            status="running",
            stage="summarizing",
            idempotency_key="recover-memory",
        )
        db.add(run)
        db.flush()
        job = models.Job(
            project_id=project.id,
            idempotency_key="recover-memory",
            kind="memory",
            resource_id=run.id,
            state="running",
            current_stage="summarizing",
            attempts=0,
            lease_owner="dead-worker",
            lease_expires_at=models.utcnow() - timedelta(minutes=1),
        )
        db.add(job)
        db.commit()
        run_id, job_id = run.id, job.id

    recovered = DurableTaskRunner(factory).recover_interrupted()
    assert recovered == 1
    with factory() as db:
        job = db.get(models.Job, job_id)
        run = db.get(models.MemoryBuildRun, run_id)
        assert job is not None and run is not None
        assert job.state == "queued"
        assert job.lease_owner is None
        assert job.lease_expires_at is None
        assert job.attempts == 1
        assert run.status == "queued"


def test_durable_runner_recovers_orphaned_assistant_claim(store: tuple[Any, Any]) -> None:
    """A crash between AgentRun and Job commits must be repaired by polling."""

    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "助手恢复")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            title="恢复会话",
        )
        db.add(conversation)
        db.flush()
        run = models.AgentRun(
            project_id=project.id,
            conversation_id=conversation.id,
            idempotency_key="recover-agent",
            status="running",
            stage="calling_model",
        )
        db.add(run)
        db.flush()
        job = models.Job(
            project_id=project.id,
            idempotency_key="assistant:recover-agent",
            kind="assistant",
            resource_id=run.id,
            state="queued",
            current_stage="queued",
            lease_owner=None,
            lease_expires_at=None,
        )
        db.add(job)
        db.commit()
        run_id, job_id = run.id, job.id

    assert DurableTaskRunner(factory).recover_interrupted() == 1
    with factory() as db:
        run = db.get(models.AgentRun, run_id)
        job = db.get(models.Job, job_id)
        assert run is not None and job is not None
        assert run.status == "queued"
        assert run.stage == "queued"
        assert job.state == "queued"
        assert job.attempts == 1


def test_assistant_claim_serializes_sibling_projects_across_sessions(
    store: tuple[Any, Any],
) -> None:
    """The durable project lease blocks a second process' assistant run."""

    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "助手串行")
        conversations: list[models.AgentConversation] = []
        runs: list[models.AgentRun] = []
        jobs: list[models.Job] = []
        for index in range(2):
            conversation = models.AgentConversation(
                project_id=project.id,
                created_by_user_id=user.id,
                title=f"会话{index + 1}",
            )
            db.add(conversation)
            conversations.append(conversation)
        db.flush()
        for index, conversation in enumerate(conversations):
            run = models.AgentRun(
                project_id=project.id,
                conversation_id=conversation.id,
                idempotency_key=f"serial-agent-{index}",
                status="queued",
                stage="queued",
            )
            db.add(run)
            runs.append(run)
        db.flush()
        for index, run in enumerate(runs):
            job = models.Job(
                project_id=project.id,
                idempotency_key=f"assistant:serial-agent-{index}",
                kind="assistant",
                resource_id=run.id,
                state="queued",
                current_stage="queued",
            )
            db.add(job)
            jobs.append(job)
        db.commit()
        run_ids = [run.id for run in runs]
        job_ids = [job.id for job in jobs]

    with factory() as first_db:
        claimed = assistant_service._claim_agent_run(first_db, run_ids[0])
        assert claimed is not None
        with factory() as second_db:
            assert assistant_service._claim_agent_run(second_db, run_ids[1]) is None

    with factory() as db:
        first_job = db.get(models.Job, job_ids[0])
        second_job = db.get(models.Job, job_ids[1])
        assert first_job is not None and second_job is not None
        assert first_job.state == "running"
        assert second_job.state == "queued"


def test_assistant_lease_heartbeat_refreshes_durable_job(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "助手心跳")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            title="心跳会话",
        )
        db.add(conversation)
        db.flush()
        run = models.AgentRun(
            project_id=project.id,
            conversation_id=conversation.id,
            idempotency_key="heartbeat-agent",
            status="queued",
            stage="queued",
        )
        db.add(run)
        db.flush()
        job = models.Job(
            project_id=project.id,
            idempotency_key="assistant:heartbeat-agent",
            kind="assistant",
            resource_id=run.id,
            state="queued",
            current_stage="queued",
        )
        db.add(job)
        db.commit()
        run_id, job_id = run.id, job.id
        claimed = assistant_service._claim_agent_run(db, run_id)
        assert claimed is not None
        owner = claimed[-1]
        job.lease_expires_at = models.utcnow() + timedelta(milliseconds=20)
        db.commit()

        monkeypatch.setattr(assistant_service, "AGENT_LEASE_HEARTBEAT_SECONDS", 0.01)
        heartbeat = assistant_service._start_agent_lease_heartbeat(db, run_id, owner)
        try:
            assert _wait_until(
                lambda: _job_expiry(factory, job_id)
                > models.utcnow().replace(tzinfo=None) + timedelta(seconds=5)
            )
        finally:
            assistant_service._stop_agent_lease_heartbeat(heartbeat)


def _job_expiry(factory: Any, job_id: str):
    with factory() as db:
        job = db.get(models.Job, job_id)
        return job.lease_expires_at if job is not None else models.utcnow()


def test_memory_enqueue_is_idempotent_and_never_duplicates_job(store: tuple[Any, Any]) -> None:
    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "记忆幂等")
        chapter, _revision = _confirmed_chapter(db, project, "确认正文")
        first = create_memory_run(db, project, chapter=chapter, actor_user_id=user.id)
        second = create_memory_run(db, project, chapter=chapter, actor_user_id=user.id)
        assert first.created is True
        assert second.created is False
        assert first.run.id == second.run.id
        assert db.scalar(
            select(models.MemoryBuildRun).where(models.MemoryBuildRun.project_id == project.id)
        ) is not None
        assert len(
            db.scalars(select(models.Job).where(models.Job.project_id == project.id)).all()
        ) == 1


class _CheckpointProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.phase = "first"
        self.phase_calls = 0
        self.failed_once = False

    async def structured(
        self,
        messages: list[dict[str, str]],
        _schema: dict[str, Any],
        *,
        role: str = "extractor",
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], ProviderResponse]:
        self.phase_calls += 1
        prompt = str(messages[-1].get("content") or "")
        self.calls.append({"phase": self.phase, "prompt": prompt, "role": role})
        if self.phase == "first" and self.phase_calls == 2 and not self.failed_once:
            self.failed_once = True
            raise ProviderError("模拟第二个记忆分块失败")
        return (
            {
                "summary": f"{self.phase} 阶段摘要",
                "storylines": [],
                "character_relations": [],
                "timeline": [],
                "unresolved_threads": [],
                "characters": [],
                "plot_threads": [],
            },
            ProviderResponse(
                content="{}",
                raw={"fake": True},
                model="checkpoint-fake",
                usage={},
                request_id=f"checkpoint-{self.phase_calls}",
            ),
        )


def _memory_payload(summary: str = "记忆摘要") -> dict[str, Any]:
    return {
        "summary": summary,
        "storylines": [],
        "character_relations": [],
        "timeline": [],
        "unresolved_threads": [],
        "characters": [],
        "plot_threads": [],
    }


def test_project_memory_ignores_confirmed_chapters_without_acceptance_pointer(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine, factory = store
    calls: list[str] = []

    def fake_structured(_provider: Any, messages: list[dict[str, str]]):
        calls.append(str(messages[-1].get("content") or ""))
        return _memory_payload(), ProviderResponse(
            content="{}",
            raw={},
            model="memory-test",
            usage={},
            request_id="memory-test",
        )

    monkeypatch.setattr(memory_service, "_structured", fake_structured)
    monkeypatch.setattr(memory_service, "provider_for", lambda _profile: object())
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "只读确认正文")
        accepted, _accepted_revision = _confirmed_chapter(db, project, "已确认的潮声")
        unaccepted = models.Chapter(
            project_id=project.id,
            chapter_number=2,
            sort_order=1,
            title="未确认章节",
            status="confirmed",
            summary_status="unprocessed",
        )
        db.add(unaccepted)
        db.flush()
        draft = models.ChapterRevision(
            chapter_id=unaccepted.id,
            revision_number=1,
            content="不应进入记忆的草稿机密",
            content_hash=models.ChapterRevision.hash_content("不应进入记忆的草稿机密"),
            source_type="manual",
        )
        db.add(draft)
        db.flush()
        unaccepted.current_revision_id = draft.id
        db.commit()
        with pytest.raises(ValueError):
            create_memory_run(db, project, chapter=unaccepted, actor_user_id=user.id)
        project_run = create_memory_run(
            db,
            project,
            scope="project",
            actor_user_id=user.id,
        )

        result = execute_memory_run(db, project_run.run.id)
        assert result.status == "current"
        assert accepted.summary == "记忆摘要"
        assert unaccepted.summary is None
        assert all("不应进入记忆的草稿机密" not in prompt for prompt in calls)
        assert any("已确认的潮声" in prompt for prompt in calls)


def test_memory_final_cas_does_not_promote_result_after_epoch_change(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine, factory = store
    changed = False

    def fake_structured(_provider: Any, _messages: list[dict[str, str]]):
        nonlocal changed
        if not changed:
            changed = True
            # Simulate a concurrent accepted change that advances the
            # project's memory epoch before the provider result is promoted.
            project.memory_epoch += 1
        return _memory_payload("过期摘要"), ProviderResponse(
            content="{}",
            raw={},
            model="memory-cas-test",
            usage={},
            request_id="memory-cas-test",
        )

    monkeypatch.setattr(memory_service, "_structured", fake_structured)
    monkeypatch.setattr(memory_service, "provider_for", lambda _profile: object())
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "记忆 CAS")
        chapter, _revision = _confirmed_chapter(db, project, "会被并发修改的正文")
        created = create_memory_run(db, project, chapter=chapter, actor_user_id=user.id)
        run_id = created.run.id

        result = execute_memory_run(db, run_id)
        assert result.status == "stale"
        assert db.scalar(
            select(models.StorySummary).where(models.StorySummary.project_id == project.id)
        ) is None
        fresh_chapter = db.get(models.Chapter, chapter.id)
        job = db.scalar(select(models.Job).where(models.Job.resource_id == run_id))
        assert fresh_chapter is not None and fresh_chapter.summary_status == "queued"
        assert job is not None and job.state == "cancelled"


def test_assistant_graph_proposal_rejects_foreign_entity_reference(
    store: tuple[Any, Any],
) -> None:
    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        source_project = _project(db, user.id, "图谱源项目")
        foreign_project = _project(db, user.id, "图谱外部项目")
        foreign_character = models.Character(
            project_id=foreign_project.id,
            name="外部人物",
            role="不应被引用",
        )
        db.add(foreign_character)
        db.flush()
        change_set = models.ChangeSet(
            project_id=source_project.id,
            source_type="assistant",
            base_memory_epoch=source_project.memory_epoch,
            status="proposed",
            summary="跨项目引用",
            created_by_user_id=user.id,
        )
        db.add(change_set)
        db.flush()
        proposal = models.Proposal(
            project_id=source_project.id,
            change_set_id=change_set.id,
            operation="upsert_graph_node",
            target_type="character",
            patch_json={
                "node_type": "character",
                "ref_id": foreign_character.id,
                "label": "外部人物",
            },
            status="proposed",
            base_memory_epoch=source_project.memory_epoch,
            reason="测试隔离",
            created_by_user_id=user.id,
        )
        db.add(proposal)
        db.commit()

        with pytest.raises(LookupError, match="不属于当前项目"):
            assistant_service.apply_proposal(db, proposal, user)
        db.rollback()
        assert db.get(models.Proposal, proposal.id).status == "proposed"


def test_memory_long_text_checkpoints_and_resume_skips_completed_stage(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine, factory = store
    provider = _CheckpointProvider()
    monkeypatch.setattr(memory_service, "provider_for", lambda _profile: provider)
    with factory() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        profile.context_length = 2_000
        project = _project(db, user.id, "长正文记忆")
        chapter, _revision = _confirmed_chapter(db, project, "潮" * 9_000)
        created = create_memory_run(db, project, chapter=chapter, actor_user_id=user.id)
        run_id = created.run.id

        first_result = execute_memory_run(db, run_id)
        assert first_result.status == "failed"
        first_artifacts = db.scalars(
            select(models.MemoryBuildArtifact)
            .where(models.MemoryBuildArtifact.run_id == run_id)
            .order_by(models.MemoryBuildArtifact.created_at)
        ).all()
        first_chunk = [item for item in first_artifacts if item.stage.endswith("chunk:1")]
        assert len(first_chunk) == 1
        assert not any(item.stage.endswith("chunk:2") for item in first_artifacts)

        # A durable retry keeps the committed first checkpoint but reclaims
        # the failed job.  The fake provider is now healthy.
        run = db.get(models.MemoryBuildRun, run_id)
        chapter = db.get(models.Chapter, chapter.id)
        assert run is not None and chapter is not None
        job = db.scalar(select(models.Job).where(models.Job.resource_id == run_id))
        assert job is not None
        run.status = "queued"
        run.stage = "queued"
        run.error = None
        run.finished_at = None
        chapter.summary_status = "queued"
        job.state = "queued"
        job.current_stage = "queued"
        job.lease_owner = None
        job.lease_expires_at = None
        db.commit()

        provider.phase = "resume"
        provider.phase_calls = 0
        resumed = execute_memory_run(db, run_id)
        assert resumed.status == "current"
        resume_calls = [item for item in provider.calls if item["phase"] == "resume"]
        assert resume_calls
        assert all("<chapter_part_1>" not in item["prompt"] for item in resume_calls)

        final_artifacts = db.scalars(
            select(models.MemoryBuildArtifact)
            .where(models.MemoryBuildArtifact.run_id == run_id)
        ).all()
        assert len(final_artifacts) >= 4  # three chunks + chapter aggregate/project stage
        assert len([item for item in final_artifacts if item.stage.endswith("chunk:1")]) == 1
        assert len([item for item in final_artifacts if item.stage.endswith("chunk:2")]) == 1
        assert len([item for item in final_artifacts if item.stage.endswith("chunk:3")]) == 1


def _new_project(client: TestClient, title: str = "助手项目") -> dict[str, Any]:
    response = client.post("/api/projects", json={"title": title, "start_mode": "setup"})
    assert response.status_code == 201, response.text
    return response.json()


def _new_conversation(client: TestClient, project_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/projects/{project_id}/assistant/conversations",
        json={"title": "设定助手", "purpose": "setup", "apply_mode": "preview"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_assistant_message_idempotency_and_last_event_id_resume(api, monkeypatch) -> None:
    client, factory, _owner_id = api
    project = _new_project(client)
    conversation = _new_conversation(client, project["id"])
    conversation_id = conversation["id"]
    path = f"/api/projects/{project['id']}/assistant/conversations/{conversation_id}/messages"

    first = client.post(
        path,
        json={"content": "请记录主角设定", "idempotency_key": "message-1"},
    )
    assert first.status_code == 202, first.text
    first_payload = first.json()
    replay = client.post(
        path,
        json={"content": "请记录主角设定", "idempotency_key": "message-1"},
    )
    assert replay.status_code == 202, replay.text
    replay_payload = replay.json()
    assert replay_payload["created"] is False
    assert replay_payload["message"]["id"] == first_payload["message"]["id"]
    assert replay_payload["run"]["id"] == first_payload["run"]["id"]

    changed_replay = client.post(
        path,
        json={"content": "篡改同一幂等键", "idempotency_key": "message-1"},
    )
    assert changed_replay.status_code == 409

    second = client.post(
        path,
        json={"content": "再补充一条", "idempotency_key": "message-2"},
    )
    assert second.status_code == 202, second.text
    events = client.get(
        f"/api/projects/{project['id']}/assistant/conversations/{conversation_id}/events?after=1"
    )
    assert events.status_code == 200
    assert [event["sequence"] for event in events.json()] == [2]

    captured: dict[str, Any] = {}

    def finite_sse(_session_factory: Any, current_id: str, after: int):
        captured["conversation_id"] = current_id
        captured["after"] = after
        yield "id: 2\ndata: {\"sequence\": 2}\n\n"

    monkeypatch.setattr(assistant_router, "_sse_events", finite_sse)
    stream = client.get(
        f"/api/projects/{project['id']}/assistant/conversations/{conversation_id}/events/stream?after=0",
        headers={"Last-Event-ID": "1"},
    )
    assert stream.status_code == 200
    assert captured == {"conversation_id": conversation_id, "after": 1}
    assert "id: 2" in stream.text

    with factory() as db:
        assert len(
            db.scalars(
                select(models.AgentMessage).where(
                    models.AgentMessage.conversation_id == conversation_id,
                    models.AgentMessage.role == "user",
                )
            ).all()
        ) == 2
        assert len(
            db.scalars(
                select(models.AgentRun).where(models.AgentRun.conversation_id == conversation_id)
            ).all()
        ) == 2


def test_assistant_stream_batch_allocates_distinct_event_sequences(store) -> None:
    """Four unflushed deltas reproduce the local SQLite regression report."""

    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "流式事件序号")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            title="序号回归",
        )
        db.add(conversation)
        db.flush()
        assistant_service.add_event(db, conversation, "message_started")
        db.commit()

        for index in range(1, 5):
            assistant_service.add_event(
                db,
                conversation,
                "message_delta",
                {"index": index, "delta": f"片段{index}"},
            )
        db.commit()

        rows = db.scalars(
            select(models.AgentEvent)
            .where(models.AgentEvent.conversation_id == conversation.id)
            .order_by(models.AgentEvent.sequence)
        ).all()
        assert [row.sequence for row in rows] == [1, 2, 3, 4, 5]
        assert [row.payload_json.get("index") for row in rows[1:]] == [1, 2, 3, 4]


def test_assistant_stream_persists_four_deltas_without_sequence_conflict(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the real worker path, including its four-token commit batch."""

    _engine, factory = store

    class StreamingProvider:
        async def stream(self, _messages: list[dict[str, Any]], **_kwargs: Any):
            for chunk in ("目前", "没有", "收到具体的修改", "需求，请你"):
                yield chunk

        async def structured(
            self,
            _messages: list[dict[str, Any]],
            _schema: dict[str, Any],
            **_kwargs: Any,
        ) -> tuple[dict[str, Any], ProviderResponse]:
            return (
                {"reply": "流式回复", "proposals": []},
                ProviderResponse(
                    content="{}",
                    raw={"fake": True},
                    model="streaming-fake",
                    usage={},
                    request_id="streaming-fake",
                ),
            )

    monkeypatch.setattr(assistant_service, "provider_for", lambda _profile: StreamingProvider())
    with factory() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = _project(db, user.id, "真实流式序号")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            title="流式助手",
            provider_profile_id=profile.id,
            provider_snapshot={"model": "streaming-fake"},
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        _message, run, created = assistant_service.create_message_run(
            db,
            conversation,
            user,
            "请补齐人物动力和关系张力",
            idempotency_key="streaming-sequence-regression",
        )
        assert created is True
        assistant_service.execute_agent_run(db, run.id)

        rows = db.scalars(
            select(models.AgentEvent)
            .where(models.AgentEvent.conversation_id == conversation.id)
            .order_by(models.AgentEvent.sequence)
        ).all()
        finished_run = db.get(models.AgentRun, run.id)
        assert finished_run is not None and finished_run.status == "completed"
        assert [row.sequence for row in rows] == list(range(1, len(rows) + 1))
        assert [row.payload_json.get("index") for row in rows if row.event_type == "message_delta"] == [1, 2, 3, 4]


def test_assistant_chapter_context_uses_server_authoritative_hashes(
    store: tuple[Any, Any],
) -> None:
    _engine, factory = store
    with factory() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = _project(db, user.id, "正文权威快照")
        chapter, revision = _confirmed_chapter(db, project, "潮声穿过灯塔")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            title="正文助手",
        )
        db.add(conversation)
        db.flush()
        message = models.AgentMessage(
            project_id=project.id,
            conversation_id=conversation.id,
            sequence=1,
            role="user",
            content="改写选区",
            status="completed",
            target_json={"type": "chapter", "chapter_id": chapter.id},
            context_snapshot={
                "base_revision_id": "client-stale",
                "base_content_hash": "client-forged",
                "selection": {"start": 0, "end": 2, "hash": "short-client-hash"},
            },
        )
        db.add(message)
        db.flush()
        run = models.AgentRun(
            project_id=project.id,
            conversation_id=conversation.id,
            message_id=message.id,
            idempotency_key="authoritative-context",
            status="queued",
            stage="queued",
        )
        db.add(run)
        db.flush()

        messages, asset_ids = assistant_service._provider_messages(
            db, conversation, run, project, user, profile
        )
        final_prompt = str(messages[-1]["content"])
        assert asset_ids == []
        assert revision.id in final_prompt
        assert revision.content_hash in final_prompt
        assert hashlib.sha256("潮声".encode()).hexdigest() in final_prompt
        assert "client-forged" not in final_prompt


def _seed_character_proposals(
    factory: Any,
    project_id: str,
    owner_id: str,
    *,
    count: int = 2,
) -> tuple[list[models.Proposal], int, list[int]]:
    with factory() as db:
        project = db.get(models.Project, project_id)
        assert project is not None
        characters: list[models.Character] = []
        for index in range(count):
            character = models.Character(
                project_id=project.id,
                name=f"人物{index + 1}",
                role="待定",
            )
            db.add(character)
            characters.append(character)
        db.flush()
        base_epoch = project.memory_epoch
        change_set = models.ChangeSet(
            project_id=project.id,
            source_type="assistant",
            base_memory_epoch=base_epoch,
            status="proposed",
            summary="批量验收提案",
            created_by_user_id=owner_id,
        )
        db.add(change_set)
        db.flush()
        proposals: list[models.Proposal] = []
        for index, character in enumerate(characters):
            proposal = models.Proposal(
                project_id=project.id,
                change_set_id=change_set.id,
                operation="update_character",
                target_type="character",
                target_id=character.id,
                patch_json={"role": f"角色{index + 1}"},
                base_version=character.version,
                base_memory_epoch=base_epoch,
                status="proposed",
                reason="验收",
                created_by_user_id=owner_id,
            )
            db.add(proposal)
            proposals.append(proposal)
        db.commit()
        ids = [proposal.id for proposal in proposals]
        versions = [character.version for character in characters]
        rows = db.scalars(select(models.Proposal).where(models.Proposal.id.in_(ids))).all()
        return rows, base_epoch, versions


def test_assistant_supports_individual_and_batch_proposal_application(api) -> None:
    client, factory, owner_id = api
    project = _new_project(client, "提案项目")
    proposals, base_epoch, versions = _seed_character_proposals(
        factory, project["id"], owner_id
    )
    proposals_by_id = {str(item.id): item for item in proposals}
    first_id, second_id = [str(item.id) for item in proposals]

    one = client.post(
        f"/api/projects/{project['id']}/assistant/proposals/{first_id}/apply",
        json={"expected_version": versions[0], "expected_memory_epoch": base_epoch},
    )
    assert one.status_code == 200, one.text
    assert one.json()["status"] == "applied"

    batch = client.post(
        "/api/assistant/proposals/apply-batch",
        json={
            "proposal_ids": [second_id],
            "expected_memory_epoch": base_epoch + 1,
            "expected_versions": {second_id: versions[1]},
        },
    )
    assert batch.status_code == 200, batch.text
    assert batch.json()["applied_count"] == 1
    assert batch.json()["proposals"][0]["id"] == second_id

    with factory() as db:
        project_row = db.get(models.Project, project["id"])
        assert project_row is not None
        assert project_row.memory_epoch == base_epoch + 2
        applied = db.scalars(
            select(models.Proposal).where(models.Proposal.id.in_([first_id, second_id]))
        ).all()
        assert {row.status for row in applied} == {"applied"}
        assert db.get(models.ChangeSet, proposals_by_id[first_id].change_set_id).status == "applied"


def _seed_chapter_proposal(
    factory: Any,
    project_id: str,
    owner_id: str,
    chapter_id: str,
    revision: models.ChapterRevision,
    *,
    selection_hash: str,
    replacement: str,
) -> tuple[str, int]:
    with factory() as db:
        project = db.get(models.Project, project_id)
        assert project is not None
        change_set = models.ChangeSet(
            project_id=project.id,
            source_type="assistant",
            base_memory_epoch=project.memory_epoch,
            status="proposed",
            summary="正文选区提案",
            created_by_user_id=owner_id,
        )
        db.add(change_set)
        db.flush()
        proposal = models.Proposal(
            project_id=project.id,
            change_set_id=change_set.id,
            operation="edit_chapter_selection",
            target_type="chapter",
            target_id=chapter_id,
            patch_json={
                "base_revision_id": revision.id,
                "base_content_hash": revision.content_hash,
                "selection_start": 0,
                "selection_end": 2,
                "selection_hash": selection_hash,
                "replacement": replacement,
            },
            base_memory_epoch=project.memory_epoch,
            status="proposed",
            reason="正文验收",
            created_by_user_id=owner_id,
        )
        db.add(proposal)
        db.commit()
        return proposal.id, project.memory_epoch


def test_assistant_selection_conflict_and_applied_edit_creates_review_bundle(api) -> None:
    client, factory, owner_id = api
    project = _new_project(client, "正文提案项目")
    chapter_response = client.post(
        f"/api/projects/{project['id']}/chapters",
        json={"chapter_number": 1, "title": "第一章", "content": "ABCD, keep tail"},
    )
    assert chapter_response.status_code == 201, chapter_response.text
    chapter = chapter_response.json()
    confirmed = client.post(f"/api/chapters/{chapter['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    with factory() as db:
        revision = db.get(models.ChapterRevision, chapter["current_revision_id"])
        assert revision is not None
        chapter_row = db.get(models.Chapter, chapter["id"])
        assert chapter_row is not None
        epoch = db.get(models.Project, project["id"]).memory_epoch
        assert epoch == project["memory_epoch"] + 1

    bad_id, bad_epoch = _seed_chapter_proposal(
        factory,
        project["id"],
        owner_id,
        chapter["id"],
        revision,
        selection_hash="not-the-selected-text",
        replacement="XY",
    )
    conflict = client.post(
        f"/api/assistant/proposals/{bad_id}/apply",
        json={"expected_memory_epoch": bad_epoch},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "proposal_conflict"
    with factory() as db:
        bad = db.get(models.Proposal, bad_id)
        current = db.get(models.Chapter, chapter["id"])
        assert bad is not None and current is not None
        assert bad.status == "conflict"
        assert current.current_revision_id == revision.id

    valid_hash = hashlib.sha256(revision.content[:2].encode("utf-8")).hexdigest()
    valid_id, valid_epoch = _seed_chapter_proposal(
        factory,
        project["id"],
        owner_id,
        chapter["id"],
        revision,
        selection_hash=valid_hash,
        replacement="XY",
    )
    applied = client.post(
        f"/api/assistant/proposals/{valid_id}/apply",
        json={"expected_memory_epoch": valid_epoch},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"

    with factory() as db:
        current = db.get(models.Chapter, chapter["id"])
        assert current is not None
        assert current.status == "needs_review"
        new_revision = db.get(models.ChapterRevision, current.current_revision_id)
        old_revision = db.get(models.ChapterRevision, revision.id)
        assert new_revision is not None and old_revision is not None
        assert new_revision.content == "XYCD, keep tail"
        assert old_revision.content == "ABCD, keep tail"
        bundle = db.scalar(
            select(models.ReviewBundle).where(
                models.ReviewBundle.chapter_id == chapter["id"],
                models.ReviewBundle.draft_revision_id == new_revision.id,
            )
        )
        assert bundle is not None
        assert bundle.status in {"pending", "needs_review"}
        assert bundle.draft_revision_id == new_revision.id


def test_minimal_0002_sqlite_migrates_to_story_workspace_head(tmp_path: Path) -> None:
    """A users-only legacy file must still reach the current migration head."""

    database = tmp_path / "minimal-0002.sqlite3"
    engine = create_engine_for_url(f"sqlite:///{database.as_posix()}")
    timestamp = "2026-08-31 12:00:00"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE users (
                    id VARCHAR(36) PRIMARY KEY,
                    email VARCHAR(320) NOT NULL,
                    email_normalized VARCHAR(320) NOT NULL,
                    display_name VARCHAR(120),
                    password_hash VARCHAR(512),
                    is_email_verified BOOLEAN NOT NULL,
                    is_active BOOLEAN NOT NULL,
                    default_provider_id VARCHAR(36),
                    failed_login_attempts INTEGER NOT NULL,
                    locked_until DATETIME,
                    last_login_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX ix_users_email_normalized "
                "ON users (email_normalized)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
            connection.execute(text("INSERT INTO alembic_version VALUES ('20260901_0002')"))
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id,email,email_normalized,password_hash,is_email_verified,is_active," 
                    "failed_login_attempts,created_at,updated_at) VALUES "
                    "('legacy-user','legacy@example.test','legacy@example.test','hash',1,1,0," 
                    ":created,:updated)"
                ),
                {"created": timestamp, "updated": timestamp},
            )

        run_migrations(engine)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"characters", "memory_build_runs", "agent_conversations"} <= tables
        assert "auto_summary_enabled" in {
            column["name"] for column in inspector.get_columns("users")
        }
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "20260901_0004"
            )
    finally:
        engine.dispose()


__all__ = []
