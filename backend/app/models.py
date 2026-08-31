"""Relational story-canon model.

The database is the source of truth.  In particular, revisions are append
only: editing a chapter always creates a new ``ChapterRevision`` row.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


def json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title = synonym("name")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_bible: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_encoding: Mapped[str | None] = mapped_column(String(40), nullable=True)
    genre: Mapped[str | None] = mapped_column(String(120), nullable=True)
    viewpoint: Mapped[str | None] = mapped_column(String(120), nullable=True)
    style: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    must_happen: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    must_not_happen: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    hard_constraints: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    outline: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    canon_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    memory_epoch: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    needs_rebuild: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    chapters: Mapped[list[Chapter]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Chapter.sort_order"
    )
    canon_items: Mapped[list[CanonItem]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    timeline_events: Mapped[list[TimelineEvent]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    plot_threads: Mapped[list[PlotThread]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    generation_runs: Mapped[list[GenerationRun]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    review_bundles: Mapped[list[ReviewBundle]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[Job]] = relationship(back_populates="project", cascade="all, delete-orphan")
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    import_sources: Mapped[list[ImportSource]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("project_id", "chapter_number", name="uq_chapter_project_number"),
        Index("ix_chapters_project_order", "project_id", "sort_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    volume_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal = synonym("chapter_number")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="未命名章节", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    summary_status: Mapped[str] = mapped_column(String(40), default="current", nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    accepted_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="chapters")
    revisions: Mapped[list[ChapterRevision]] = relationship(
        back_populates="chapter",
        cascade="all, delete-orphan",
        order_by="ChapterRevision.revision_number",
    )


class ChapterRevision(Base):
    __tablename__ = "chapter_revisions"
    __table_args__ = (
        UniqueConstraint("chapter_id", "revision_number", name="uq_revision_chapter_number"),
        Index("ix_revisions_chapter_created", "chapter_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chapter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    parent_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_generated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    chapter: Mapped[Chapter] = relationship(back_populates="revisions")

    @staticmethod
    def hash_content(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    revision_no = synonym("revision_number")


class CanonItem(Base):
    __tablename__ = "canon_items"
    __table_args__ = (
        Index("ix_canon_project_status", "project_id", "status"),
        Index("ix_canon_source_revision", "source_revision_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(80), default="general", nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[Any] = mapped_column(JSON, default=dict, nullable=False)
    value_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    is_hard: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_from: Mapped[str | None] = mapped_column(String(120), nullable=True)
    valid_to: Mapped[str | None] = mapped_column(String(120), nullable=True)
    canon_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="canon_items")


class TimelineEvent(Base):
    __tablename__ = "timeline_events"
    __table_args__ = (Index("ix_timeline_project_sequence", "project_id", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    story_time: Mapped[str | None] = mapped_column(String(120), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), default="event", nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="confirmed", nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="timeline_events")


class PlotThread(Base):
    __tablename__ = "plot_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    thread_type: Mapped[str] = mapped_column(String(40), default="main", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    planted_at: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payoff_at: Mapped[str | None] = mapped_column(String(80), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="plot_threads")


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_generation_project_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    review_bundle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="running", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    model_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="generation_runs")
    artifacts: Mapped[list[GenerationArtifact]] = relationship(
        back_populates="generation_run", cascade="all, delete-orphan"
    )


class GenerationArtifact(Base):
    __tablename__ = "generation_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("generation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    generation_run: Mapped[GenerationRun] = relationship(back_populates="artifacts")


class ReviewBundle(Base):
    __tablename__ = "review_bundles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    base_canon_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    base_memory_epoch: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    draft_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    canon_changes: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    audit_issues: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    source_context: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    force_accept_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="review_bundles")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_job_project_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(40), default="queued", nullable=False, index=True)
    current_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="jobs")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor: Mapped[str] = mapped_column(String(80), default="system", nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project | None] = relationship(back_populates="audit_logs")


class ProviderProfile(Base):
    __tablename__ = "provider_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(
        String(500), default="http://127.0.0.1:1234/v1", nullable=False
    )
    protocol: Mapped[str] = mapped_column(String(40), default="chat_completions", nullable=False)
    model_role_mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    context_length: Mapped[int] = mapped_column(Integer, default=8192, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Only a credential-manager reference is stored here, never the secret.
    api_key_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ImportSource(Base):
    __tablename__ = "import_sources"
    __table_args__ = (UniqueConstraint("project_id", "source_hash", name="uq_import_project_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    encoding: Mapped[str | None] = mapped_column(String(40), nullable=True)
    stored_name: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="import_sources")


@event.listens_for(ChapterRevision, "before_insert")
def _set_revision_hash(_mapper: Any, _connection: Any, target: ChapterRevision) -> None:
    if not target.content_hash:
        target.content_hash = ChapterRevision.hash_content(target.content or "")


@event.listens_for(ChapterRevision, "before_update")
def _reject_revision_update(_mapper: Any, _connection: Any, _target: ChapterRevision) -> None:
    """Keep historical正文 immutable even when a caller uses the ORM directly."""

    raise ValueError("ChapterRevision 不可修改；请新增修订")


@event.listens_for(CanonItem, "before_insert")
@event.listens_for(CanonItem, "before_update")
def _sync_canon_text(_mapper: Any, _connection: Any, target: CanonItem) -> None:
    """Keep the FTS-friendly textual projection in sync with JSON values."""

    target.value_text = json_text(target.value)


__all__ = [
    "AuditLog",
    "CanonItem",
    "Chapter",
    "ChapterRevision",
    "GenerationArtifact",
    "GenerationRun",
    "Job",
    "PlotThread",
    "Project",
    "ProviderProfile",
    "ReviewBundle",
    "TimelineEvent",
    "json_text",
    "new_id",
    "utcnow",
]
