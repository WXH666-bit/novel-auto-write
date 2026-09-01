"""Small database-backed task dispatcher for generation, memory, and Agent work."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from .common import utcnow

TERMINAL_JOB_STATES = {"completed", "failed", "cancelled", "awaiting_review"}


class DurableTaskRunner:
    """Poll durable jobs and dispatch at most one active task per project.

    Individual workflow services still own the database lease.  The dispatcher
    only decides which candidate gets a local worker, so duplicate app
    processes remain harmless and workflow-specific idempotency stays central.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        workers: int = 1,
        recovery_interval_seconds: float = 5.0,
    ) -> None:
        self.session_factory = session_factory
        self.workers = max(1, int(workers))
        # Keep recovery frequent enough to repair a crashed worker without
        # turning the dispatcher into a busy loop.  ``start`` still performs
        # an immediate recovery for the application boot path.
        self.recovery_interval_seconds = max(0.1, float(recovery_interval_seconds))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._running_jobs: set[str] = set()
        self._running_projects: set[str] = set()
        self._worker_threads: set[threading.Thread] = set()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.recover_interrupted()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="novel-durable-task-dispatcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        # Worker calls are durable but still share this process's session
        # factory.  Let in-flight calls finish briefly before an app reload or
        # test fixture swaps the engine underneath them.  Any job that outlives
        # this grace period remains protected by its database lease and will be
        # recovered by the next process.
        deadline = time.monotonic() + 10.0
        while True:
            with self._lock:
                workers = [thread for thread in self._worker_threads if thread.is_alive()]
            if not workers:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            workers[0].join(timeout=min(remaining, 0.5))

    def recover_interrupted(self) -> int:
        """Requeue work whose lease expired while the application was down."""

        from ..models import AgentRun, GenerationRun, Job, MemoryBuildRun

        now = utcnow()
        with self.session_factory() as session:
            expired_lease = or_(
                Job.lease_owner.is_(None),
                Job.lease_expires_at.is_(None),
                Job.lease_expires_at < now,
            )
            # A process can die after claiming AgentRun but before updating
            # its Job.  Such a job remains ``queued`` even though it can no
            # longer be dispatched; include only this assistant orphan case,
            # rather than re-selecting every ordinary queued job on each poll.
            orphan_assistant = and_(
                Job.kind == "assistant",
                Job.state == "queued",
                expired_lease,
                exists(
                    select(AgentRun.id).where(
                        AgentRun.id == Job.resource_id,
                        AgentRun.status.in_(
                            ("running", "calling_model", "awaiting_model")
                        ),
                    )
                ),
            )
            jobs = session.scalars(
                select(Job).where(
                    or_(
                        and_(
                            Job.state.notin_(TERMINAL_JOB_STATES | {"queued", "needs_retry"}),
                            expired_lease,
                        ),
                        orphan_assistant,
                    )
                )
            ).all()
            recovered = 0
            for job in jobs:
                kind = str(getattr(job, "kind", None) or "generation")
                resource_id = getattr(job, "resource_id", None)
                linked_run: Any | None = None
                if kind == "generation":
                    linked_run = (
                        session.get(GenerationRun, resource_id)
                        if resource_id
                        else session.scalar(
                            select(GenerationRun).where(
                                GenerationRun.project_id == job.project_id,
                                GenerationRun.idempotency_key == job.idempotency_key,
                            )
                        )
                    )
                elif kind == "memory" and resource_id:
                    linked_run = session.get(MemoryBuildRun, resource_id)
                elif kind == "assistant" and resource_id:
                    linked_run = session.get(AgentRun, resource_id)

                # Reconcile a job whose worker died after committing the run
                # result.  Re-queueing a terminal run would leave a phantom
                # queued job that no dispatcher can execute.
                terminal_status = getattr(linked_run, "status", None)
                if terminal_status in {"completed", "current"}:
                    job.state = "completed"
                    job.current_stage = "completed"
                elif terminal_status in {"cancelled", "awaiting_review"}:
                    job.state = terminal_status
                    job.current_stage = terminal_status
                elif terminal_status == "failed":
                    job.state = "failed"
                    job.current_stage = "failed"
                else:
                    next_attempt = int(getattr(job, "attempts", 0) or 0) + 1
                    max_attempts = max(1, int(getattr(job, "max_attempts", 3) or 3))
                    job.attempts = next_attempt
                    if next_attempt >= max_attempts:
                        job.state = "failed"
                        job.current_stage = "failed"
                        job.last_error = "任务租约反复失效，已达到最大重试次数"
                        if linked_run is not None:
                            linked_run.status = "failed"
                            if hasattr(linked_run, "stage"):
                                linked_run.stage = "failed"
                            if hasattr(linked_run, "error"):
                                linked_run.error = job.last_error
                    else:
                        job.state = "queued"
                        job.current_stage = "queued"
                        if linked_run is not None:
                            linked_run.status = "queued"
                            if hasattr(linked_run, "stage"):
                                linked_run.stage = "queued"
                            if hasattr(linked_run, "error"):
                                linked_run.error = None
                job.lease_owner = None
                job.lease_expires_at = None
                recovered += 1
            session.commit()
            return recovered

    def _loop(self) -> None:
        last_recovery = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_recovery >= self.recovery_interval_seconds:
                try:
                    self.recover_interrupted()
                except Exception:
                    # A transient database outage must not permanently kill
                    # the dispatcher thread; the next interval retries it.
                    pass
                last_recovery = now
            try:
                self.run_once()
            except Exception:
                # Candidate inspection is best-effort.  Individual workflow
                # services own their failure state and the next poll can
                # safely retry after a transient connection error.
                pass
            self._stop.wait(0.5)

    def _candidates(self, session: Session) -> list[Any]:
        from ..models import Job

        now = utcnow()
        return list(
            session.scalars(
                select(Job)
                .where(
                    Job.state == "queued",
                    or_(
                        Job.lease_owner.is_(None),
                        Job.lease_expires_at.is_(None),
                        Job.lease_expires_at < now,
                    ),
                )
                .order_by(Job.created_at)
                .limit(max(4, self.workers * 4))
            ).all()
        )

    def run_once(self) -> int:
        """Dispatch available jobs once; exposed for deterministic tests."""

        with self._lock:
            available = self.workers - len(self._running_jobs)
            local_projects = set(self._running_projects)
        if available <= 0:
            return 0
        with self.session_factory() as session:
            candidates = self._candidates(session)
            now = utcnow()
            chosen: list[tuple[str, str]] = []
            for job in candidates:
                if len(chosen) >= available or job.project_id in local_projects:
                    continue
                # A leased sibling means another process already owns this
                # project's serial execution slot.
                sibling = session.scalar(
                    select(type(job).id).where(
                        type(job).project_id == job.project_id,
                        type(job).id != job.id,
                        type(job).lease_owner.is_not(None),
                        type(job).lease_expires_at > now,
                        type(job).state.notin_(TERMINAL_JOB_STATES),
                    )
                )
                if sibling is not None:
                    continue
                chosen.append((str(job.id), str(job.project_id)))
                local_projects.add(str(job.project_id))
        for job_id, project_id in chosen:
            with self._lock:
                if job_id in self._running_jobs or project_id in self._running_projects:
                    continue
                self._running_jobs.add(job_id)
                self._running_projects.add(project_id)
            worker = threading.Thread(
                target=self._run_job,
                args=(job_id, project_id),
                name=f"novel-job-{job_id[:8]}",
                daemon=True,
            )
            with self._lock:
                self._worker_threads.add(worker)
            worker.start()
        return len(chosen)

    def _run_job(self, job_id: str, project_id: str) -> None:
        try:
            with self.session_factory() as session:
                self._dispatch(session, job_id)
        finally:
            with self._lock:
                self._running_jobs.discard(job_id)
                self._running_projects.discard(project_id)
                self._worker_threads.discard(threading.current_thread())

    @staticmethod
    def _dispatch(session: Session, job_id: str) -> None:
        from ..models import GenerationRun, Job

        job = session.get(Job, job_id)
        if job is None or job.state != "queued":
            return
        kind = str(getattr(job, "kind", None) or "generation")
        resource_id = getattr(job, "resource_id", None)
        if kind == "memory":
            from .memory import execute_memory_run

            execute_memory_run(
                session,
                str(resource_id or (job.payload or {}).get("memory_run_id")),
            )
            return
        if kind == "assistant":
            from .assistant import execute_agent_run

            execute_agent_run(
                session,
                str(resource_id or (job.payload or {}).get("agent_run_id")),
            )
            return
        from .generation import execute_generation

        run = (
            session.get(GenerationRun, resource_id)
            if resource_id
            else session.scalar(
                select(GenerationRun).where(
                    GenerationRun.project_id == job.project_id,
                    GenerationRun.idempotency_key == job.idempotency_key,
                )
            )
        )
        if run is None:
            job.state = "failed"
            job.last_error = "生成任务记录不存在"
            session.commit()
            return
        execute_generation(session, str(run.id))


def default_worker_count(database_url: str) -> int:
    return 2 if str(database_url).lower().startswith("mysql") else 1


__all__ = ["DurableTaskRunner", "default_worker_count"]
