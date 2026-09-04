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
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from backend.app import db as db_module
from backend.app import models
from backend.app.db import Base, create_engine_for_url, get_db, init_db, run_migrations
from backend.app.main import app
from backend.app.routers import assistant as assistant_router
from backend.app.services import assistant as assistant_service
from backend.app.services import generation as generation_service
from backend.app.services import memory as memory_service
from backend.app.services.memory import create_memory_run, execute_memory_run
from backend.app.services.providers import (
    ProviderError,
    ProviderResponse,
    StructuredOutputError,
)
from backend.app.services.tasks import DurableTaskRunner, default_worker_count
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


def test_sqlite_pool_modes_survive_concurrent_short_connections(tmp_path: Path) -> None:
    """File SQLite uses short-lived pools without racing WAL initialization."""

    file_engine = create_engine_for_url(f"sqlite:///{(tmp_path / 'short.sqlite3').as_posix()}")
    memory_engine = create_engine_for_url("sqlite:///:memory:")
    try:
        assert isinstance(file_engine.pool, NullPool)
        assert isinstance(memory_engine.pool, StaticPool)

        def read_pragmas(_index: int) -> tuple[Any, Any, Any]:
            with file_engine.connect() as connection:
                return (
                    connection.execute(text("PRAGMA journal_mode")).scalar(),
                    connection.execute(text("PRAGMA busy_timeout")).scalar(),
                    connection.execute(text("PRAGMA foreign_keys")).scalar(),
                )

        with ThreadPoolExecutor(max_workers=12) as executor:
            values = list(executor.map(read_pragmas, range(48)))
        assert all(mode == "wal" and timeout == 5000 and foreign_keys == 1 for mode, timeout, foreign_keys in values)
    finally:
        memory_engine.dispose()
        file_engine.dispose()


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


def test_durable_runner_uses_workers_across_projects_without_queue_starvation(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        crowded_project = _project(db, user.id, "排队较多的小说")
        other_project = _project(db, user.id, "应并行的小说")
        crowded_jobs: list[models.Job] = []
        base_time = models.utcnow() - timedelta(minutes=1)
        for index in range(12):
            crowded_jobs.append(
                models.Job(
                    project_id=crowded_project.id,
                    idempotency_key=f"crowded-{index}",
                    kind="generation",
                    state="queued",
                    created_at=base_time + timedelta(seconds=index),
                )
            )
        other_job = models.Job(
            project_id=other_project.id,
            idempotency_key="other-project",
            kind="generation",
            state="queued",
            created_at=base_time + timedelta(seconds=30),
        )
        db.add_all([*crowded_jobs, other_job])
        db.commit()
        expected_ids = {crowded_jobs[0].id, other_job.id}

    dispatched: list[str] = []
    dispatched_lock = threading.Lock()
    both_started = threading.Event()
    release = threading.Event()

    def fake_dispatch(_session: Any, job_id: str) -> None:
        with dispatched_lock:
            dispatched.append(job_id)
            if len(dispatched) == 2:
                both_started.set()
        release.wait(timeout=3)

    monkeypatch.setattr(DurableTaskRunner, "_dispatch", staticmethod(fake_dispatch))
    runner = DurableTaskRunner(factory, workers=2)
    try:
        # Only the oldest runnable job from each project is selected.  A large
        # backlog for one novel must not consume the candidate query's LIMIT.
        assert runner.run_once() == 2
        assert both_started.wait(timeout=2)
        assert set(dispatched) == expected_ids
        assert runner.run_once() == 0
    finally:
        release.set()
        runner.stop()


def test_default_worker_count_parallelizes_mysql_only() -> None:
    assert default_worker_count(" mysql+pymysql://novel:test@db/novel ") == 4
    assert default_worker_count("sqlite:///data/novel.sqlite3") == 1


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


def test_assistant_claim_repairs_run_without_job(store: tuple[Any, Any]) -> None:
    """Legacy AgentRuns acquire a lease only after a durable Job is created."""

    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "助手孤儿运行")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            title="无任务会话",
        )
        db.add(conversation)
        db.flush()
        run = models.AgentRun(
            project_id=project.id,
            conversation_id=conversation.id,
            idempotency_key="orphan-agent-run",
            status="queued",
            stage="queued",
        )
        db.add(run)
        db.commit()
        run_id = run.id

        claimed = assistant_service._claim_agent_run(db, run_id)
        assert claimed is not None
        _run, _conversation, claimed_project, _user, job, _lease_owner = claimed
        assert job is not None
        assert claimed_project.id == project.id
        assert job.resource_id == run_id
        assert job.state == "running"


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


class _RetryingMemoryProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def structured(
        self,
        _messages: list[dict[str, str]],
        _schema: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[dict[str, Any], ProviderResponse]:
        self.calls += 1
        if self.calls <= 2:
            raise StructuredOutputError("模拟结构化输出解析失败")
        return (
            _memory_payload("自动重试后的记忆摘要"),
            ProviderResponse(
                content="{}",
                raw={"fake": True},
                model="memory-retry-test",
                usage={},
                request_id=f"memory-retry-{self.calls}",
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


def test_memory_model_errors_retry_in_background_and_keep_progress(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine, factory = store
    provider = _RetryingMemoryProvider()
    observed_timeout: dict[str, float] = {}

    def fake_provider_for(_profile: Any, **kwargs: Any) -> _RetryingMemoryProvider:
        observed_timeout["seconds"] = float(kwargs["request_timeout_seconds"])
        return provider

    monkeypatch.setattr(memory_service, "provider_for", fake_provider_for)
    monkeypatch.setattr(memory_service, "MEMORY_PROVIDER_RETRY_DELAYS", (0.0, 0.0))
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "后台自动重试")
        chapter, _revision = _confirmed_chapter(db, project, "雨夜里灯塔重新亮起。")
        created = create_memory_run(db, project, chapter=chapter, actor_user_id=user.id)

        result = execute_memory_run(db, created.run.id)

        assert result.status == "current"
        assert result.error is None
        assert provider.calls == 4  # chapter: 3 attempts; project roll-up: 1 attempt
        assert observed_timeout["seconds"] >= 180
        job = db.scalar(select(models.Job).where(models.Job.resource_id == created.run.id))
        assert job is not None
        assert job.state == "completed"
        assert job.payload["memory_model_retry_total"] == 2
        assert memory_service._memory_progress(
            type(
                "RetryingRun",
                (),
                {"status": "running", "stage": "retrying:2:3:chapters:1:4"},
            )()
        ) == (26, "模型响应异常，正在后台自动重试（2/3）")


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
    monkeypatch.setattr(
        memory_service,
        "provider_for",
        lambda _profile, **_kwargs: object(),
    )
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
    monkeypatch.setattr(
        memory_service,
        "provider_for",
        lambda _profile, **_kwargs: object(),
    )
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
    monkeypatch.setattr(
        memory_service,
        "provider_for",
        lambda _profile, **_kwargs: provider,
    )
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


def test_assistant_conversation_reports_effective_provider(api) -> None:
    client, _factory, _owner_id = api
    project = _new_project(client, "会话 Provider 展示")
    conversation = _new_conversation(client, project["id"])
    assert conversation["provider_name"] == "测试模型"
    assert conversation["provider_model"] == "fake-model"
    assert conversation["provider_available"] is True
    assert conversation["provider_capabilities"]["vision"] is False


def test_assistant_conversation_keeps_selected_model_identity_when_disabled(api) -> None:
    client, factory, owner_id = api
    with factory() as db:
        profile = models.ProviderProfile(
            owner_id=owner_id,
            name="章节精修模型",
            base_url="http://127.0.0.1:9999/v1",
            protocol="chat_completions",
            model_role_mapping={"assistant": "editor-model"},
            enabled=True,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        profile_id = profile.id

    project = _new_project(client, "指定会话模型")
    created = client.post(
        f"/api/projects/{project['id']}/assistant/conversations",
        json={"purpose": "chapter", "provider_profile_id": profile_id},
    )
    assert created.status_code == 201, created.text
    conversation = created.json()
    assert conversation["provider_profile_id"] == profile_id
    assert conversation["provider_name"] == "章节精修模型"
    assert conversation["provider_model"] == "editor-model"
    assert conversation["provider_available"] is True

    with factory() as db:
        stored = db.get(models.ProviderProfile, profile_id)
        assert stored is not None
        stored.model_role_mapping = {"assistant": "replacement-model"}
        stored.enabled = False
        db.commit()

    loaded = client.get(
        f"/api/projects/{project['id']}/assistant/conversations/{conversation['id']}"
    )
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["provider_name"] == "章节精修模型"
    assert loaded.json()["provider_model"] == "editor-model"
    assert loaded.json()["provider_available"] is False

    with factory() as db:
        stored_conversation = db.get(models.AgentConversation, conversation["id"])
        stored_profile = db.get(models.ProviderProfile, profile_id)
        assert stored_conversation is not None
        assert stored_profile is not None
        runtime_profile = assistant_service._runtime_provider_profile(
            stored_conversation,
            stored_profile,
        )
        assert runtime_profile.model_role_mapping["assistant"] == "editor-model"
        assert runtime_profile.base_url == stored_profile.base_url


def test_first_user_turn_names_default_conversation(api) -> None:
    client, _factory, _owner_id = api
    project = _new_project(client, "历史标题")
    created = client.post(
        f"/api/projects/{project['id']}/assistant/conversations",
        json={},
    )
    assert created.status_code == 201, created.text
    conversation = created.json()
    response = client.post(
        f"/api/projects/{project['id']}/assistant/conversations/{conversation['id']}/messages",
        json={
            "content": "让林渡在本章结尾发现第二座灯塔，然后停在悬念处。",
            "idempotency_key": "history-title",
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["conversation"]["title"] == "让林渡在本章结尾发现第二座灯塔，然后停在悬念…"


def test_assistant_compacts_older_turns_into_durable_conversation_memory(store) -> None:
    _engine, factory = store
    with factory() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = _project(db, user.id, "长对话")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            provider_profile_id=profile.id,
            title="持续写作",
        )
        db.add(conversation)
        db.flush()
        rows: list[models.AgentMessage] = []
        for sequence in range(1, 25):
            row = models.AgentMessage(
                project_id=project.id,
                conversation_id=conversation.id,
                sequence=sequence,
                role="user" if sequence % 2 else "assistant",
                content=f"第 {sequence} 轮关于灯塔守则的决定",
                status="completed",
            )
            rows.append(row)
            db.add(row)
        db.flush()
        run = models.AgentRun(
            project_id=project.id,
            conversation_id=conversation.id,
            message_id=rows[-1].id,
            idempotency_key="long-memory",
            status="queued",
            stage="queued",
        )
        db.add(run)
        db.flush()

        messages, _asset_ids = assistant_service._provider_messages(
            db, conversation, run, project, user, profile
        )

        memory = conversation.context_snapshot["conversation_memory"]
        assert "第 1 轮关于灯塔守则的决定" in memory
        assert conversation.context_snapshot["memory_through_sequence"] == 6
        assert any("压缩协作记录" in str(item["content"]) for item in messages)
        assert str(messages[-1]["content"]).startswith("第 24 轮")


def test_user_can_cancel_a_queued_assistant_run(store) -> None:
    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "停止任务")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
        )
        db.add(conversation)
        db.commit()
        message, run, _created = assistant_service.create_message_run(
            db,
            conversation,
            user,
            "先停一下",
            idempotency_key="cancel-run",
        )

        stopped = assistant_service.cancel_assistant_run(
            db, conversation, run, user
        )

        assert stopped.status == "cancelled"
        assert stopped.stage == "cancelled"
        job = db.get(models.Job, stopped.job_id)
        assert job is not None
        assert job.state == "cancelled"
        events = db.scalars(
            select(models.AgentEvent).where(models.AgentEvent.run_id == stopped.id)
        ).all()
        assert events[-1].event_type == "run.cancelled"
        assert message.content == "先停一下"


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


def test_sqlite_concurrent_assistant_messages_keep_sequences_unique(store) -> None:
    """SQLite's process guard covers the row-lock semantics it does not provide."""

    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "并发会话序号")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            title="并发写入",
        )
        db.add(conversation)
        db.commit()
        user_id = user.id
        conversation_id = conversation.id

    workers = 8
    barrier = threading.Barrier(workers)

    def submit(index: int) -> tuple[int, int]:
        with factory() as db:
            user = db.get(models.User, user_id)
            conversation = db.get(models.AgentConversation, conversation_id)
            assert user is not None and conversation is not None
            barrier.wait(timeout=5)
            message, run, created = assistant_service.create_message_run(
                db,
                conversation,
                user,
                f"并发消息 {index}",
                idempotency_key=f"concurrent-message-{index}",
            )
            assert created is True
            return message.sequence, int((run.input_snapshot or {}).get("attempt", 1))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(submit, range(workers)))

    assert sorted(sequence for sequence, _attempt in results) == list(
        range(1, workers + 1)
    )
    with factory() as db:
        messages = db.scalars(
            select(models.AgentMessage)
            .where(
                models.AgentMessage.conversation_id == conversation_id,
                models.AgentMessage.role == "user",
            )
            .order_by(models.AgentMessage.sequence)
        ).all()
        events = db.scalars(
            select(models.AgentEvent)
            .where(models.AgentEvent.conversation_id == conversation_id)
            .order_by(models.AgentEvent.sequence)
        ).all()
        assert [message.sequence for message in messages] == list(
            range(1, workers + 1)
        )
        assert [event.sequence for event in events] == list(range(1, workers + 1))


def test_sqlite_worker_event_and_new_message_share_transaction_guard(store) -> None:
    """A worker's uncommitted event must serialize with an incoming message."""

    _engine, factory = store
    with factory() as db:
        user, _profile = seed_tenant(db)
        project = _project(db, user.id, "运行中并发会话")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            title="运行中继续对话",
        )
        db.add(conversation)
        db.commit()
        user_id = user.id
        conversation_id = conversation.id

    worker_allocated = threading.Event()
    allow_worker_commit = threading.Event()
    api_started = threading.Event()

    def persist_worker_event() -> int:
        with factory() as db:
            conversation = db.get(models.AgentConversation, conversation_id)
            assert conversation is not None
            event = assistant_service.add_event(
                db,
                conversation,
                "message.delta",
                {"delta": "worker"},
            )
            worker_allocated.set()
            assert allow_worker_commit.wait(timeout=5)
            db.commit()
            return event.sequence

    def submit_message() -> int:
        with factory() as db:
            user = db.get(models.User, user_id)
            conversation = db.get(models.AgentConversation, conversation_id)
            assert user is not None and conversation is not None
            api_started.set()
            message, _run, created = assistant_service.create_message_run(
                db,
                conversation,
                user,
                "运行中追加的消息",
                idempotency_key="worker-api-overlap",
            )
            assert created is True
            return message.sequence

    with ThreadPoolExecutor(max_workers=2) as executor:
        worker_future = executor.submit(persist_worker_event)
        assert worker_allocated.wait(timeout=5)
        api_future = executor.submit(submit_message)
        assert api_started.wait(timeout=5)
        time.sleep(0.1)
        assert not api_future.done()
        allow_worker_commit.set()
        assert worker_future.result(timeout=5) == 1
        assert api_future.result(timeout=5) == 1

    with factory() as db:
        events = db.scalars(
            select(models.AgentEvent)
            .where(models.AgentEvent.conversation_id == conversation_id)
            .order_by(models.AgentEvent.sequence)
        ).all()
        assert [event.sequence for event in events] == [1, 2]


def test_assistant_stream_persists_four_deltas_without_sequence_conflict(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise coalesced streaming persistence and contiguous event ids."""

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
        delta_rows = [row for row in rows if row.event_type == "message.delta"]
        assert len(delta_rows) == 1
        assert delta_rows[0].payload_json["delta"] == "目前没有收到具体的修改需求，请你"
        assert delta_rows[0].payload_json["start_index"] == 1
        assert delta_rows[0].payload_json["end_index"] == 4
        assistant_message = db.scalar(
            select(models.AgentMessage).where(
                models.AgentMessage.conversation_id == conversation.id,
                models.AgentMessage.role == "assistant",
            )
        )
        assert assistant_message is not None
        assert assistant_message.content == "目前没有收到具体的修改需求，请你"


def test_assistant_stream_coalesces_many_small_deltas(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hundreds of provider chunks become a small, complete durable log."""

    _engine, factory = store

    class SmallChunkProvider:
        async def stream(self, _messages: list[dict[str, Any]], **_kwargs: Any):
            for _index in range(448):
                yield "字"

        async def structured(
            self,
            _messages: list[dict[str, Any]],
            _schema: dict[str, Any],
            **_kwargs: Any,
        ) -> tuple[dict[str, Any], ProviderResponse]:
            return (
                {"reply": "提取完成", "proposals": []},
                ProviderResponse(
                    content="{}",
                    raw={},
                    model="small-chunk-fake",
                    usage={},
                    request_id="small-chunk-fake",
                ),
            )

    monkeypatch.setattr(assistant_service, "provider_for", lambda _profile: SmallChunkProvider())
    with factory() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = _project(db, user.id, "流式合并压力")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            provider_profile_id=profile.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        _message, run, _created = assistant_service.create_message_run(
            db,
            conversation,
            user,
            "请写一段连续回复",
            idempotency_key="coalesced-small-chunks",
        )

        assistant_service.execute_agent_run(db, run.id)
        rows = db.scalars(
            select(models.AgentEvent)
            .where(models.AgentEvent.conversation_id == conversation.id)
            .order_by(models.AgentEvent.sequence)
        ).all()
        delta_rows = [row for row in rows if row.event_type == "message.delta"]
        assert 1 <= len(delta_rows) <= 8
        assert "".join(row.payload_json["delta"] for row in delta_rows) == "字" * 448
        assert [row.sequence for row in rows] == list(range(1, len(rows) + 1))
        assistant_message = db.scalar(
            select(models.AgentMessage).where(
                models.AgentMessage.run_id == run.id,
                models.AgentMessage.role == "assistant",
            )
        )
        assert assistant_message is not None
        assert assistant_message.content == "字" * 448


def test_assistant_plain_reply_keeps_markdown_list_as_prose() -> None:
    """A leading Markdown list is not mistaken for a JSON machine envelope."""

    assert assistant_service._plain_reply("[第一项]\n[第二项]") == "[第一项]\n[第二项]"


@pytest.mark.parametrize(
    ("chunks", "expected", "expected_status"),
    [
        (
            ['{"reply":"安全', '回复","proposals":[]}'],
            "安全回复",
            "completed",
        ),
        (
            ["```text\n", "安全回复", "\n```"],
            "安全回复",
            "completed",
        ),
        (
            ["```json\n{\"reply\":\"安全回复\",\"proposals\":[}", "\n```"],
            "安全回复",
            "completed",
        ),
        (
            ['{"reply":"半'],
            assistant_service.INCOMPLETE_REPLY_MESSAGE,
            "needs_retry",
        ),
    ],
)
def test_assistant_stream_machine_wrappers_never_become_delta_text(
    store: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[str],
    expected: str,
    expected_status: str,
) -> None:
    """JSON/fence streams are normalized before any durable delta is sent."""

    _engine, factory = store

    class WrappedStreamingProvider:
        async def stream(self, _messages: list[dict[str, Any]], **_kwargs: Any):
            for chunk in chunks:
                yield chunk

        async def structured(
            self,
            _messages: list[dict[str, Any]],
            _schema: dict[str, Any],
            **_kwargs: Any,
        ) -> tuple[dict[str, Any], ProviderResponse]:
            return (
                {"reply": "结构化提取结果", "proposals": []},
                ProviderResponse(
                    content="{}",
                    raw={},
                    model="wrapped-stream-fake",
                    usage={},
                    request_id="wrapped-stream-fake",
                ),
            )

    monkeypatch.setattr(
        assistant_service, "provider_for", lambda _profile: WrappedStreamingProvider()
    )
    with factory() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = _project(db, user.id, "流式包装门禁")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            provider_profile_id=profile.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        _message, run, _created = assistant_service.create_message_run(
            db,
            conversation,
            user,
            "请返回安全文本",
            idempotency_key=f"wrapped-stream-{expected_status}",
        )
        assistant_service.execute_agent_run(db, run.id)
        db.refresh(run)
        rows = db.scalars(
            select(models.AgentEvent)
            .where(models.AgentEvent.conversation_id == conversation.id)
            .order_by(models.AgentEvent.sequence)
        ).all()
        assert run.status == expected_status
        assert not [row for row in rows if row.event_type == "message.delta"]
        assistant_message = db.scalar(
            select(models.AgentMessage).where(
                models.AgentMessage.run_id == run.id,
                models.AgentMessage.role == "assistant",
            )
        )
        assert assistant_message is not None
        assert assistant_message.content == expected
        if expected_status == "completed":
            replacements = [row for row in rows if row.event_type == "message.replace"]
            assert replacements
            assert replacements[-1].payload_json["content"] == expected


def test_assistant_non_stream_uses_prose_then_structured_extraction(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structured extraction is a second call and cannot replace prose."""

    _engine, factory = store
    calls: list[tuple[str, str]] = []

    class NonStreamingProvider:
        async def complete(self, messages: list[dict[str, Any]], **_kwargs: Any) -> ProviderResponse:
            calls.append(("complete", str(messages[-1]["role"])))
            return ProviderResponse(
                content="先给用户的普通中文回复",
                raw={},
                model="non-stream-fake",
                usage={},
                request_id="non-stream-fake",
            )

        async def structured(
            self,
            messages: list[dict[str, Any]],
            _schema: dict[str, Any],
            **_kwargs: Any,
        ) -> tuple[dict[str, Any], ProviderResponse]:
            calls.append(("structured", str(messages[-2]["content"])))
            return (
                {"reply": "提取器不应覆盖普通回复", "proposals": []},
                ProviderResponse(
                    content="{}",
                    raw={},
                    model="extractor-fake",
                    usage={},
                    request_id="extractor-fake",
                ),
            )

    monkeypatch.setattr(assistant_service, "provider_for", lambda _profile: NonStreamingProvider())
    with factory() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = _project(db, user.id, "非流式双通道")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            provider_profile_id=profile.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        _message, run, _created = assistant_service.create_message_run(
            db,
            conversation,
            user,
            "请解释当前设定",
            idempotency_key="non-stream-two-step",
        )
        assistant_service.execute_agent_run(db, run.id)
        assert calls[0] == ("complete", "user")
        assert calls[1] == ("structured", "先给用户的普通中文回复")
        assistant_message = db.scalar(
            select(models.AgentMessage).where(
                models.AgentMessage.run_id == run.id,
                models.AgentMessage.role == "assistant",
            )
        )
        assert assistant_message is not None
        assert assistant_message.content == "先给用户的普通中文回复"


def test_assistant_retry_carries_attempt_into_new_events(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A durable retry advances the event attempt without changing run id."""

    _engine, factory = store
    calls = 0

    class RetryProvider:
        async def complete(self, _messages: list[dict[str, Any]], **_kwargs: Any) -> ProviderResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ProviderError("临时模型错误", retryable=True)
            return ProviderResponse(
                content="重试后的普通中文回复",
                raw={},
                model="retry-fake",
                usage={},
                request_id="retry-fake",
            )

        async def structured(
            self,
            _messages: list[dict[str, Any]],
            _schema: dict[str, Any],
            **_kwargs: Any,
        ) -> tuple[dict[str, Any], ProviderResponse]:
            return (
                {"reply": "重试后的普通中文回复", "proposals": []},
                ProviderResponse(
                    content="{}",
                    raw={},
                    model="retry-extractor",
                    usage={},
                    request_id="retry-extractor",
                ),
            )

    provider = RetryProvider()
    monkeypatch.setattr(assistant_service, "provider_for", lambda _profile: provider)
    with factory() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = _project(db, user.id, "助手重试事件")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            provider_profile_id=profile.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        _message, run, _created = assistant_service.create_message_run(
            db,
            conversation,
            user,
            "请稍后重试",
            idempotency_key="attempt-protocol",
        )
        assistant_service.execute_agent_run(db, run.id)
        db.refresh(run)
        job = db.get(models.Job, run.job_id)
        assert job is not None and run.status == "needs_retry" and job.attempts == 1
        assistant_rows = db.scalars(
            select(models.AgentMessage).where(
                models.AgentMessage.run_id == run.id,
                models.AgentMessage.role == "assistant",
            )
        ).all()
        assert len(assistant_rows) == 1
        first_last_sequence = db.scalar(
            select(func.max(models.AgentEvent.sequence)).where(
                models.AgentEvent.conversation_id == conversation.id
            )
        )

        run.status = "queued"
        run.stage = "queued"
        job.state = "queued"
        job.current_stage = "queued"
        db.commit()
        assistant_service.execute_agent_run(db, run.id)
        rows = db.scalars(
            select(models.AgentEvent)
            .where(
                models.AgentEvent.conversation_id == conversation.id,
                models.AgentEvent.sequence > int(first_last_sequence or 0),
            )
            .order_by(models.AgentEvent.sequence)
        ).all()
        assert rows
        assert {row.payload_json.get("attempt") for row in rows} == {2}
        assistant_rows = db.scalars(
            select(models.AgentMessage).where(
                models.AgentMessage.run_id == run.id,
                models.AgentMessage.role == "assistant",
            )
        ).all()
        assert len(assistant_rows) == 1
        assert assistant_rows[0].content == "重试后的普通中文回复"


def test_assistant_incomplete_json_reply_is_retryable_and_safe(
    store: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial reply string is hidden behind a retry-safe Chinese message."""

    _engine, factory = store

    class MalformedProvider:
        async def complete(self, _messages: list[dict[str, Any]], **_kwargs: Any) -> ProviderResponse:
            return ProviderResponse(
                content='{"reply":"\\u4f60',
                raw={},
                model="malformed-fake",
                usage={},
                request_id="malformed-fake",
            )

        async def structured(self, *_args: Any, **_kwargs: Any):
            raise AssertionError("不应在普通回复格式错误后调用提取器")

    monkeypatch.setattr(assistant_service, "provider_for", lambda _profile: MalformedProvider())
    with factory() as db:
        user, profile = seed_tenant(db)
        assert profile is not None
        project = _project(db, user.id, "助手格式恢复")
        conversation = models.AgentConversation(
            project_id=project.id,
            created_by_user_id=user.id,
            provider_profile_id=profile.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        _message, run, _created = assistant_service.create_message_run(
            db,
            conversation,
            user,
            "请回答",
            idempotency_key="incomplete-json",
        )
        assistant_service.execute_agent_run(db, run.id)
        db.refresh(run)
        assert run.status == "needs_retry"
        assert run.error == assistant_service.INCOMPLETE_REPLY_MESSAGE
        assistant_message = db.scalar(
            select(models.AgentMessage).where(
                models.AgentMessage.run_id == run.id,
                models.AgentMessage.role == "assistant",
            )
        )
        assert assistant_message is not None
        assert assistant_message.content == assistant_service.INCOMPLETE_REPLY_MESSAGE
        assert "proposals" not in assistant_message.content


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


def test_assistant_selection_conflict_and_applied_edit_stays_completable(api) -> None:
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
        assert current.status == "draft"
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
        assert bundle is None

    completed = client.post(
        f"/api/chapters/{chapter['id']}/complete",
        json={"analyze": False},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["chapter"]["status"] == "confirmed"


def test_global_diff_accepts_chapter_and_queues_project_memory(api) -> None:
    client, factory, owner_id = api
    project = _new_project(client, "全书协作项目")
    created = client.post(
        f"/api/projects/{project['id']}/chapters",
        json={"chapter_number": 1, "title": "第一章", "content": "旧正文"},
    )
    assert created.status_code == 201, created.text
    chapter = created.json()
    confirmed = client.post(f"/api/chapters/{chapter['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text

    with factory() as db:
        project_row = db.get(models.Project, project["id"])
        chapter_row = db.get(models.Chapter, chapter["id"])
        revision = db.get(models.ChapterRevision, chapter_row.current_revision_id)
        assert project_row is not None and chapter_row is not None and revision is not None
        base_epoch = project_row.memory_epoch
        conversation = models.AgentConversation(
            project_id=project_row.id,
            created_by_user_id=owner_id,
            purpose="global",
            title="贯穿全书修改",
        )
        db.add(conversation)
        db.flush()
        user_message = models.AgentMessage(
            project_id=project_row.id,
            conversation_id=conversation.id,
            sequence=1,
            role="user",
            content="统一调整第一章伏笔",
            status="completed",
        )
        db.add(user_message)
        db.flush()
        run = models.AgentRun(
            project_id=project_row.id,
            conversation_id=conversation.id,
            message_id=user_message.id,
            idempotency_key="global-diff-test",
            status="completed",
            stage="completed",
        )
        db.add(run)
        db.flush()
        change_set = models.ChangeSet(
            project_id=project_row.id,
            source_type="assistant",
            source_id=run.id,
            base_memory_epoch=base_epoch,
            status="proposed",
            summary="第一章改动",
            created_by_user_id=owner_id,
        )
        db.add(change_set)
        db.flush()
        proposal = models.Proposal(
            project_id=project_row.id,
            change_set_id=change_set.id,
            operation="edit_chapter",
            target_type="chapter",
            target_id=chapter_row.id,
            scope_chapter_id=chapter_row.id,
            patch_json={
                "base_revision_id": revision.id,
                "base_content_hash": revision.content_hash,
                "selection_start": 0,
                "selection_end": len(revision.content),
                "selection_hash": revision.content_hash,
                "replacement": "新正文，伏笔已经统一。",
            },
            base_memory_epoch=base_epoch,
            status="proposed",
            reason="全书协作",
            created_by_user_id=owner_id,
        )
        db.add(proposal)
        db.commit()
        proposal_id = proposal.id

    accepted = client.post(
        "/api/assistant/proposals/apply-batch",
        json={
            "proposal_ids": [proposal_id],
            "expected_memory_epoch": base_epoch,
        },
    )
    assert accepted.status_code == 200, accepted.text
    payload = accepted.json()
    assert payload["proposals"][0]["status"] == "applied"
    assert payload["memory_run"]["scope"] == "project"
    assert payload["memory_run"]["status"] == "queued"
    assert payload["memory_run"]["progress"] >= 0

    with factory() as db:
        project_row = db.get(models.Project, project["id"])
        chapter_row = db.get(models.Chapter, chapter["id"])
        current = db.get(models.ChapterRevision, chapter_row.current_revision_id)
        assert project_row is not None and chapter_row is not None and current is not None
        assert current.content == "新正文，伏笔已经统一。"
        assert chapter_row.accepted_revision_id == current.id
        assert chapter_row.status == "confirmed"
        assert project_row.memory_epoch == base_epoch + 1
        assert db.scalar(
            select(models.MemoryBuildRun).where(
                models.MemoryBuildRun.id == payload["memory_run"]["id"]
            )
        ) is not None


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
                "20260903_0008"
            )
    finally:
        engine.dispose()


def test_0005_story_graph_rows_are_backfilled_into_current_chapter(tmp_path: Path) -> None:
    database = tmp_path / "chapter-scope-0005.sqlite3"
    engine = create_engine_for_url(f"sqlite:///{database.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, current_chapter_id VARCHAR(36))"
            )
            connection.exec_driver_sql(
                "CREATE TABLE chapters (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL)"
            )
            connection.exec_driver_sql(
                """
                CREATE TABLE story_graph_nodes (
                    id VARCHAR(36) PRIMARY KEY,
                    project_id VARCHAR(36) NOT NULL,
                    node_type VARCHAR(40) NOT NULL,
                    ref_id VARCHAR(36),
                    chapter_id VARCHAR(36),
                    CONSTRAINT uq_story_graph_node_ref UNIQUE (project_id, node_type, ref_id)
                )
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TABLE story_graph_edges (
                    id VARCHAR(36) PRIMARY KEY,
                    project_id VARCHAR(36) NOT NULL,
                    source_node_id VARCHAR(36) NOT NULL,
                    target_node_id VARCHAR(36) NOT NULL,
                    relation_type VARCHAR(80) NOT NULL,
                    CONSTRAINT uq_story_graph_edge_relation UNIQUE
                    (project_id, source_node_id, target_node_id, relation_type)
                )
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TABLE story_graph_layouts (
                    id VARCHAR(36) PRIMARY KEY,
                    project_id VARCHAR(36) NOT NULL,
                    CONSTRAINT uq_story_graph_layout_project UNIQUE (project_id)
                )
                """
            )
            connection.exec_driver_sql(
                "CREATE TABLE proposals (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "INSERT INTO alembic_version VALUES ('20260902_0005')"
            )
            connection.exec_driver_sql(
                "INSERT INTO projects VALUES ('project-1', 'chapter-1')"
            )
            connection.exec_driver_sql(
                "INSERT INTO chapters VALUES ('chapter-1', 'project-1'), ('chapter-2', 'project-1')"
            )
            connection.exec_driver_sql(
                "INSERT INTO story_graph_nodes VALUES "
                "('node-1', 'project-1', 'chapter', 'chapter-2', 'chapter-2'), "
                "('node-2', 'project-1', 'custom', 'clue-1', NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO story_graph_edges VALUES "
                "('edge-1', 'project-1', 'node-1', 'node-2', 'related')"
            )
            connection.exec_driver_sql(
                "INSERT INTO story_graph_layouts VALUES ('layout-1', 'project-1')"
            )
            connection.exec_driver_sql(
                "INSERT INTO proposals VALUES ('proposal-1', 'project-1')"
            )

        run_migrations(engine)

        schema = inspect(engine)
        for table in (
            "story_graph_nodes",
            "story_graph_edges",
            "story_graph_layouts",
            "proposals",
        ):
            assert "scope_chapter_id" in {
                column["name"] for column in schema.get_columns(table)
            }
            assert any(
                foreign_key.get("referred_table") == "chapters"
                and foreign_key.get("constrained_columns") == ["scope_chapter_id"]
                for foreign_key in schema.get_foreign_keys(table)
            )
        assert {
            constraint["name"]
            for constraint in schema.get_unique_constraints("story_graph_nodes")
        } == {"uq_story_graph_node_chapter_ref"}
        assert {
            constraint["name"]
            for constraint in schema.get_unique_constraints("story_graph_edges")
        } == {"uq_story_graph_edge_chapter_relation"}
        assert {
            constraint["name"]
            for constraint in schema.get_unique_constraints("story_graph_layouts")
        } == {"uq_story_graph_layout_project_chapter"}
        with engine.begin() as connection:
            for table in (
                "story_graph_nodes",
                "story_graph_edges",
                "story_graph_layouts",
                "proposals",
            ):
                scopes = connection.execute(
                    text(f"SELECT DISTINCT scope_chapter_id FROM {table}")
                ).scalars().all()
                assert scopes == ["chapter-1"]
            connection.exec_driver_sql(
                "INSERT INTO story_graph_nodes "
                "(id, project_id, scope_chapter_id, node_type, ref_id, chapter_id) "
                "VALUES ('node-3', 'project-1', 'chapter-2', 'custom', 'clue-1', NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO story_graph_layouts (id, project_id, scope_chapter_id) "
                "VALUES ('layout-2', 'project-1', 'chapter-2')"
            )
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
                ).scalar_one() == "20260903_0008"
    finally:
        engine.dispose()


__all__ = []
