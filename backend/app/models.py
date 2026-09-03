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
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym
from sqlalchemy.types import JSON

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


def story_summary_scope_key(scope: str | None, chapter_id: str | None) -> str:
    """Return the non-null uniqueness key for a project/chapter summary.

    MySQL treats NULL values as distinct in a UNIQUE constraint, so a
    ``(project_id, scope, chapter_id)`` key cannot enforce one project-level
    summary.  Persisting the scope in one non-null value makes the invariant
    portable across SQLite and MySQL while keeping the original columns for
    filtering and API compatibility.
    """

    normalized_scope = str(scope or "project").strip().lower() or "project"
    if normalized_scope == "project" or chapter_id is None:
        return normalized_scope
    return f"{normalized_scope}:{chapter_id}"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
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
    story_summaries: Mapped[list[StorySummary]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    memory_build_runs: Mapped[list[MemoryBuildRun]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    characters: Mapped[list[Character]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Character.created_at"
    )
    change_sets: Mapped[list[ChangeSet]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    proposals: Mapped[list[Proposal]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    graph_nodes: Mapped[list[StoryGraphNode]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    graph_edges: Mapped[list[StoryGraphEdge]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    graph_layouts: Mapped[list[StoryGraphLayout]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    media_assets: Mapped[list[MediaAsset]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    agent_conversations: Mapped[list[AgentConversation]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    owner: Mapped[User] = relationship(back_populates="projects")


class User(Base):
    """An application account and the root tenant boundary.

    ``email_normalized`` is the value used for identity comparisons.  The
    display/original email is retained separately so changing presentation
    casing does not change an account's identity.  ``password_hash`` is
    nullable only for the disabled legacy owner created while upgrading old
    single-user databases.
    """

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email_normalized", "email_normalized", unique=True),
        Index("ix_users_username_normalized", "username_normalized", unique=True),
        CheckConstraint(
            "email_normalized IS NOT NULL OR username_normalized IS NOT NULL",
            name="ck_users_identity_present",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # Email remains the identity in the default deployment mode, but is
    # nullable so username-only deployments do not collect an email address.
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_normalized: Mapped[str | None] = mapped_column(String(320), nullable=True)
    username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    username_normalized: Mapped[str | None] = mapped_column(String(120), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # A provider is selected explicitly by the user.  This is intentionally a
    # plain ID instead of an FK to avoid a circular table dependency during
    # upgrades and to allow safe provider deletion.
    default_provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Account-level switch for derived story memory.  Projects may still expose
    # a manual rebuild action, but new/continued writing honours this setting.
    auto_summary_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    preferences_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    projects: Mapped[list[Project]] = relationship(back_populates="owner")
    provider_profiles: Mapped[list[ProviderProfile]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    email_tokens: Mapped[list[EmailToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    """Opaque server-side session; only a SHA-256 token digest is persisted."""

    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_user_active", "user_id", "revoked_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class EmailToken(Base):
    """Single-use email verification/password reset token digest."""

    __tablename__ = "email_tokens"
    __table_args__ = (
        Index("ix_email_tokens_user_purpose", "user_id", "purpose", "used_at"),
        Index("ix_email_tokens_expires", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="email_tokens")


class AuthRateLimit(Base):
    """Small DB-backed limiter that works across application restarts/workers."""

    __tablename__ = "auth_rate_limits"
    __table_args__ = (UniqueConstraint("action", "key_hash", name="uq_auth_rate_action_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
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
    # Provider selection is frozen at task creation.  The credential itself is
    # deliberately not part of this snapshot.
    provider_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    provider_protocol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
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
    # Derived-memory candidates are kept separate from canon changes so a
    # review can display/approve them without silently mutating the story.
    summary_candidate: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_candidates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
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
    kind: Mapped[str] = mapped_column(
        String(30), default="generation", server_default="generation", nullable=False, index=True
    )
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, server_default="3", nullable=False
    )
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
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
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
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(
        String(500), default="https://api.openai.com/v1", nullable=False
    )
    protocol: Mapped[str] = mapped_column(String(40), default="chat_completions", nullable=False)
    api_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anthropic_workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    model_role_mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    context_length: Mapped[int] = mapped_column(Integer, default=8192, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Only a credential-manager reference is stored here, never the secret.
    api_key_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="provider_profiles")


class SearchDocument(Base):
    """Portable derived search document.

    SQLite deployments materialise this data into the FTS5 virtual tables.
    MySQL deployments use this table as the source for a FULLTEXT ngram index.
    Only accepted chapter revisions and confirmed canon are written by the
    rebuild service; keeping that policy outside the model makes migrations
    and rebuilds idempotent.
    """

    __tablename__ = "search_documents"
    __table_args__ = (
        Index("ix_search_documents_owner_project", "owner_id", "project_id"),
        Index("ix_search_documents_source", "source_type", "source_id"),
        UniqueConstraint(
            "owner_id", "project_id", "source_type", "source_id", "revision_id",
            name="uq_search_documents_source_revision",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
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
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="import_sources")


class StorySummary(Base):
    """The current derived memory snapshot for one project or chapter."""

    __tablename__ = "story_summaries"
    __table_args__ = (
        UniqueConstraint("project_id", "scope_key", name="uq_story_summary_project_scope_key"),
        Index("ix_story_summaries_project_scope", "project_id", "scope", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(String(40), default="project", nullable=False)
    chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope_key: Mapped[str] = mapped_column(String(300), nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="current", nullable=False, index=True)
    summary_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    structured_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    memory_epoch: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    def __init__(self, **kwargs: Any) -> None:
        # A Python-side constructor default keeps direct ORM callers safe as
        # well as the memory service, including chapter summaries created by
        # older integrations that do not know about ``scope_key`` yet.
        kwargs.setdefault(
            "scope_key",
            story_summary_scope_key(kwargs.get("scope"), kwargs.get("chapter_id")),
        )
        super().__init__(**kwargs)

    project: Mapped[Project] = relationship(back_populates="story_summaries")
    revisions: Mapped[list[StorySummaryRevision]] = relationship(
        back_populates="story_summary",
        cascade="all, delete-orphan",
        order_by="StorySummaryRevision.created_at",
    )


class StorySummaryRevision(Base):
    """Append-only provenance row for a derived summary snapshot."""

    __tablename__ = "story_summary_revisions"
    __table_args__ = (Index("ix_story_summary_revisions_source", "source_revision_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    story_summary_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("story_summaries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    summary_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    structured_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    provider_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    model_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    memory_epoch: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    story_summary: Mapped[StorySummary] = relationship(back_populates="revisions")


class MemoryBuildRun(Base):
    """Durable lifecycle for a summary/memory extraction job."""

    __tablename__ = "memory_build_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_memory_build_project_idempotency"),
        Index("ix_memory_build_runs_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(40), default="project", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(50), default="queued", nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="memory_build_runs")
    artifacts: Mapped[list[MemoryBuildArtifact]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="MemoryBuildArtifact.created_at"
    )


class MemoryBuildArtifact(Base):
    """Checkpointed output from a memory build, safe to resume by stage."""

    __tablename__ = "memory_build_artifacts"
    __table_args__ = (Index("ix_memory_build_artifacts_run_stage", "run_id", "stage"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_build_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    run: Mapped[MemoryBuildRun] = relationship(back_populates="artifacts")


class Character(Base):
    """A first-class, project-isolated character card."""

    __tablename__ = "characters"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_character_project_name"),
        Index("ix_characters_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pronouns: Mapped[str | None] = mapped_column(String(80), nullable=True)
    age: Mapped[str | None] = mapped_column(String(80), nullable=True)
    occupation: Mapped[str | None] = mapped_column(String(160), nullable=True)
    appearance: Mapped[str | None] = mapped_column(Text, nullable=True)
    personality: Mapped[str | None] = mapped_column(Text, nullable=True)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    goals: Mapped[str | None] = mapped_column(Text, nullable=True)
    motivation: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict_fears: Mapped[str | None] = mapped_column(Text, nullable=True)
    abilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    arc: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    image_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="characters")
    revisions: Mapped[list[CharacterRevision]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        order_by="CharacterRevision.revision_number",
    )

    @property
    def goal(self) -> str | None:
        """Singular compatibility alias used by compact character forms."""

        return self.goals

    @goal.setter
    def goal(self, value: str | None) -> None:
        self.goals = value

    @property
    def conflict(self) -> str | None:
        return self.conflict_fears

    @conflict.setter
    def conflict(self, value: str | None) -> None:
        self.conflict_fears = value


class CharacterRevision(Base):
    """Append-only character-card revision with the normal form fields."""

    __tablename__ = "character_revisions"
    __table_args__ = (
        UniqueConstraint("character_id", "revision_number", name="uq_character_revision_number"),
        Index("ix_character_revisions_created", "character_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    character_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pronouns: Mapped[str | None] = mapped_column(String(80), nullable=True)
    age: Mapped[str | None] = mapped_column(String(80), nullable=True)
    occupation: Mapped[str | None] = mapped_column(String(160), nullable=True)
    appearance: Mapped[str | None] = mapped_column(Text, nullable=True)
    personality: Mapped[str | None] = mapped_column(Text, nullable=True)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    goals: Mapped[str | None] = mapped_column(Text, nullable=True)
    motivation: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict_fears: Mapped[str | None] = mapped_column(Text, nullable=True)
    abilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    arc: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    image_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_type: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    source_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    character: Mapped[Character] = relationship(back_populates="revisions")

    @property
    def goal(self) -> str | None:
        return self.goals

    @property
    def conflict(self) -> str | None:
        return self.conflict_fears


class ChangeSet(Base):
    """A reviewable set of user/agent mutations against one story version."""

    __tablename__ = "change_sets"
    __table_args__ = (Index("ix_change_sets_project_status", "project_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(40), default="assistant", nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    base_memory_epoch: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="proposed", nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    changes_json: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="change_sets")
    proposals: Mapped[list[Proposal]] = relationship(
        back_populates="change_set", cascade="all, delete-orphan", order_by="Proposal.created_at"
    )


class Proposal(Base):
    """One allow-listed mutation inside a ChangeSet."""

    __tablename__ = "proposals"
    __table_args__ = (Index("ix_proposals_project_status", "project_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    change_set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("change_sets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    scope_chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    patch_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_memory_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="proposed", nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="proposals")
    change_set: Mapped[ChangeSet] = relationship(back_populates="proposals")


class StoryGraphNode(Base):
    """A chapter-scoped graph node, optionally backed by a story entity."""

    __tablename__ = "story_graph_nodes"
    __table_args__ = (
        Index(
            "ix_story_graph_nodes_project_chapter_type",
            "project_id",
            "scope_chapter_id",
            "node_type",
        ),
        UniqueConstraint(
            "project_id",
            "scope_chapter_id",
            "node_type",
            "ref_id",
            name="uq_story_graph_node_chapter_ref",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    node_type: Mapped[str] = mapped_column(String(40), default="custom", nullable=False)
    ref_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    character_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    plot_thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    position_x: Mapped[float | None] = mapped_column(nullable=True)
    position_y: Mapped[float | None] = mapped_column(nullable=True)
    width: Mapped[float | None] = mapped_column(nullable=True)
    height: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="graph_nodes")
    outgoing_edges: Mapped[list[StoryGraphEdge]] = relationship(
        foreign_keys="StoryGraphEdge.source_node_id",
        back_populates="source",
        cascade="all, delete-orphan",
    )
    incoming_edges: Mapped[list[StoryGraphEdge]] = relationship(
        foreign_keys="StoryGraphEdge.target_node_id",
        back_populates="target",
        cascade="all, delete-orphan",
    )


class StoryGraphEdge(Base):
    """A relationship between two nodes in the same project chapter."""

    __tablename__ = "story_graph_edges"
    __table_args__ = (
        Index(
            "ix_story_graph_edges_project_chapter",
            "project_id",
            "scope_chapter_id",
            "relation_type",
        ),
        UniqueConstraint(
            "project_id",
            "scope_chapter_id",
            "source_node_id",
            "target_node_id",
            "relation_type",
            name="uq_story_graph_edge_chapter_relation",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source_node_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("story_graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_node_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("story_graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(80), default="related", nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    directed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    weight: Mapped[float | None] = mapped_column(nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="graph_edges")
    source: Mapped[StoryGraphNode] = relationship(
        foreign_keys=[source_node_id], back_populates="outgoing_edges"
    )
    target: Mapped[StoryGraphNode] = relationship(
        foreign_keys=[target_node_id], back_populates="incoming_edges"
    )


class StoryGraphLayout(Base):
    """Saved viewport/layout state for one chapter graph view."""

    __tablename__ = "story_graph_layouts"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "scope_chapter_id",
            name="uq_story_graph_layout_project_chapter",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_chapter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    layout_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="graph_layouts")


class MediaAsset(Base):
    """Tenant-owned binary metadata; bytes live under the configured data dir."""

    __tablename__ = "media_assets"
    __table_args__ = (
        Index("ix_media_assets_project_kind", "project_id", "kind"),
        Index("ix_media_assets_owner_project", "owner_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), default="character", nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), default="upload", nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    extension: Mapped[str] = mapped_column(String(10), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    alt_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="media_assets")


class AgentConversation(Base):
    """Project-scoped assistant thread with replayable durable state."""

    __tablename__ = "agent_conversations"
    __table_args__ = (Index("ix_agent_conversations_project_updated", "project_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="故事设定助手", nullable=False)
    purpose: Mapped[str] = mapped_column(String(80), default="setup", nullable=False)
    apply_mode: Mapped[str] = mapped_column(String(40), default="auto_draft", nullable=False)
    provider_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    provider_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="agent_conversations")
    messages: Mapped[list[AgentMessage]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="AgentMessage.sequence"
    )
    runs: Mapped[list[AgentRun]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="AgentRun.created_at"
    )
    events: Mapped[list[AgentEvent]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="AgentEvent.sequence"
    )


class AgentMessage(Base):
    """A user/assistant message preserved independently of provider uptime."""

    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "idempotency_key", name="uq_agent_message_idempotency"),
        Index("ix_agent_messages_project_sequence", "project_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="completed", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    usage_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    target_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    authorized_asset_ids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    conversation: Mapped[AgentConversation] = relationship(back_populates="messages")


class AgentRun(Base):
    """One assistant execution, optionally linked to a generic Job row."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("conversation_id", "idempotency_key", name="uq_agent_run_idempotency"),
        Index("ix_agent_runs_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(50), default="queued", nullable=False)
    provider_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    provider_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[AgentConversation] = relationship(back_populates="runs")
    events: Mapped[list[AgentEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentEvent.sequence"
    )
    tool_calls: Mapped[list[AgentToolCall]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentToolCall.created_at"
    )


class AgentEvent(Base):
    """Append-only event log used by the live right-hand assistant panel."""

    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_agent_event_sequence"),
        Index("ix_agent_events_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    conversation: Mapped[AgentConversation] = relationship(back_populates="events")
    run: Mapped[AgentRun | None] = relationship(back_populates="events")


class AgentToolCall(Base):
    """Auditable structured tool invocation emitted by an assistant run."""

    __tablename__ = "agent_tool_calls"
    __table_args__ = (Index("ix_agent_tool_calls_project_created", "project_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="completed", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    run: Mapped[AgentRun] = relationship(back_populates="tool_calls")


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


@event.listens_for(StorySummary, "before_insert")
@event.listens_for(StorySummary, "before_update")
def _sync_story_summary_scope_key(
    _mapper: Any, _connection: Any, target: StorySummary
) -> None:
    """Recompute the portable uniqueness key if scope fields are edited."""

    target.scope_key = story_summary_scope_key(target.scope, target.chapter_id)


__all__ = [
    "AgentConversation",
    "AgentEvent",
    "AgentMessage",
    "AgentRun",
    "AgentToolCall",
    "AuditLog",
    "AuthRateLimit",
    "Character",
    "CharacterRevision",
    "CanonItem",
    "ChangeSet",
    "Chapter",
    "ChapterRevision",
    "EmailToken",
    "GenerationArtifact",
    "GenerationRun",
    "Job",
    "PlotThread",
    "Project",
    "Proposal",
    "ProviderProfile",
    "ReviewBundle",
    "SearchDocument",
    "MediaAsset",
    "MemoryBuildArtifact",
    "MemoryBuildRun",
    "StoryGraphEdge",
    "StoryGraphLayout",
    "StoryGraphNode",
    "StorySummary",
    "StorySummaryRevision",
    "TimelineEvent",
    "User",
    "UserSession",
    "json_text",
    "new_id",
    "story_summary_scope_key",
    "utcnow",
]
