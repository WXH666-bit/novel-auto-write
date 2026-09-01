"""Pydantic request/response contracts for the local API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class UserRead(ORMModel):
    id: str
    email: str | None = None
    username: str | None = None
    display_name: str | None = None
    is_email_verified: bool
    default_provider_id: str | None = None
    created_at: datetime
    last_login_at: datetime | None = None


class RegisterRequest(BaseModel):
    # ``identifier`` is the mode-independent field.  ``email`` and
    # ``username`` remain accepted so older clients and newer username-aware
    # clients can use the same endpoint during a deployment transition.
    identifier: str | None = Field(default=None, min_length=1, max_length=320)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    username: str | None = Field(default=None, min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def require_identifier(self) -> RegisterRequest:
        if not self.identifier:
            self.identifier = self.username or self.email
        if not self.identifier:
            raise ValueError("必须提供 identifier、email 或 username")
        return self


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class ResendVerificationRequest(BaseModel):
    identifier: str | None = Field(default=None, min_length=1, max_length=320)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    username: str | None = Field(default=None, min_length=1, max_length=120)


class LoginRequest(BaseModel):
    identifier: str | None = Field(default=None, min_length=1, max_length=320)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    username: str | None = Field(default=None, min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_identifier(self) -> LoginRequest:
        if not self.identifier:
            self.identifier = self.username or self.email
        if not self.identifier:
            raise ValueError("必须提供 identifier、email 或 username")
        return self


class ForgotPasswordRequest(BaseModel):
    identifier: str | None = Field(default=None, min_length=1, max_length=320)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    username: str | None = Field(default=None, min_length=1, max_length=120)


class AuthConfigRead(BaseModel):
    mode: Literal["email", "username"]
    verification_required: bool
    password_reset_available: bool


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    # ``password`` is accepted as a backwards-compatible client alias; new
    # callers should use the explicit ``new_password`` name.
    new_password: str | None = Field(default=None, min_length=12, max_length=128)
    password: str | None = Field(default=None, min_length=12, max_length=128)

    @model_validator(mode="after")
    def require_new_password(self) -> ResetPasswordRequest:
        if not self.new_password and self.password:
            self.new_password = self.password
        if not self.new_password:
            raise ValueError("必须提供新密码")
        return self


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    revoke_other_sessions: bool = True


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class ProjectCreate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    story_bible: str | None = None
    source_hash: str | None = None
    source_filename: str | None = None
    source_encoding: str | None = None
    genre: str | None = None
    viewpoint: str | None = None
    style: str | None = None
    target_word_count: int | None = Field(default=None, ge=0)
    must_happen: list[Any] = Field(default_factory=list)
    must_not_happen: list[Any] = Field(default_factory=list)
    hard_constraints: list[Any] = Field(default_factory=list)
    outline: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_name(self) -> ProjectCreate:
        if not (self.name or self.title):
            raise ValueError("name 或 title 至少提供一个")
        if not self.name:
            self.name = self.title
        return self


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    story_bible: str | None = None
    source_hash: str | None = None
    source_filename: str | None = None
    source_encoding: str | None = None
    genre: str | None = None
    viewpoint: str | None = None
    style: str | None = None
    target_word_count: int | None = Field(default=None, ge=0)
    must_happen: list[Any] | None = None
    must_not_happen: list[Any] | None = None
    hard_constraints: list[Any] | None = None
    outline: dict[str, Any] | None = None
    needs_rebuild: bool | None = None


class ProjectRead(ORMModel):
    id: str
    name: str
    title: str
    description: str | None = None
    story_bible: str | None = None
    source_hash: str | None = None
    source_filename: str | None = None
    source_encoding: str | None = None
    genre: str | None = None
    viewpoint: str | None = None
    style: str | None = None
    target_word_count: int | None = None
    must_happen: list[Any] = Field(default_factory=list)
    must_not_happen: list[Any] = Field(default_factory=list)
    hard_constraints: list[Any] = Field(default_factory=list)
    outline: dict[str, Any] = Field(default_factory=dict)
    canon_version: int
    memory_epoch: int
    needs_rebuild: bool
    current_chapter_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ChapterCreate(BaseModel):
    volume_number: int = Field(default=1, ge=1)
    chapter_number: int | None = Field(default=None, ge=1)
    sort_order: int | None = Field(default=None, ge=0)
    title: str = Field(default="未命名章节", min_length=1, max_length=255)
    status: str = "draft"
    summary: str | None = None
    content: str | None = None


class ChapterUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = None
    content: str | None = None
    source_type: str | None = None
    is_generated: bool | None = None
    summary_status: str | None = None
    status: str | None = None
    sort_order: int | None = Field(default=None, ge=0)


class ChapterRevisionCreate(BaseModel):
    content: str = ""
    source_type: str = "manual"
    prompt_version: str | None = None
    model_name: str | None = None
    parent_revision_id: str | None = None
    is_generated: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class ChapterRevisionRead(ORMModel):
    id: str
    chapter_id: str
    revision_number: int
    content: str
    content_hash: str
    source_type: str
    prompt_version: str | None = None
    model_name: str | None = None
    parent_revision_id: str | None = None
    is_generated: bool
    extra: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ChapterRead(ORMModel):
    id: str
    project_id: str
    volume_number: int
    chapter_number: int
    sort_order: int
    title: str
    status: str
    summary: str | None = None
    summary_status: str
    current_revision_id: str | None = None
    accepted_revision_id: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    current_revision: ChapterRevisionRead | None = None


class CanonItemCreate(BaseModel):
    category: str = "general"
    key: str = Field(min_length=1, max_length=255)
    value: Any = Field(default_factory=dict)
    aliases: list[Any] = Field(default_factory=list)
    status: str = "pending"
    is_hard: bool = False
    source_revision_id: str | None = None
    source_chapter_id: str | None = None
    source_start: int | None = Field(default=None, ge=0)
    source_end: int | None = Field(default=None, ge=0)
    source_excerpt: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    note: str | None = None


class CanonItemUpdate(BaseModel):
    category: str | None = None
    key: str | None = Field(default=None, min_length=1, max_length=255)
    value: Any | None = None
    aliases: list[Any] | None = None
    status: str | None = None
    is_hard: bool | None = None
    source_revision_id: str | None = None
    source_chapter_id: str | None = None
    source_start: int | None = Field(default=None, ge=0)
    source_end: int | None = Field(default=None, ge=0)
    source_excerpt: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    note: str | None = None


class CanonItemRead(ORMModel):
    id: str
    project_id: str
    category: str
    key: str
    value: Any
    aliases: list[Any] = Field(default_factory=list)
    value_text: str
    status: str
    is_hard: bool
    source_revision_id: str | None = None
    source_chapter_id: str | None = None
    source_start: int | None = None
    source_end: int | None = None
    source_excerpt: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    canon_version: int | None = None
    confidence: float | None = None
    note: str | None = None
    superseded_by_id: str | None = None
    created_at: datetime
    updated_at: datetime


class CanonConfirmRequest(BaseModel):
    reason: str | None = None
    force: bool = False


class TimelineEventRead(ORMModel):
    id: str
    project_id: str
    chapter_id: str | None = None
    source_revision_id: str | None = None
    sequence: int
    story_time: str | None = None
    event_type: str
    title: str
    description: str | None = None
    status: str
    needs_review: bool
    created_at: datetime


class PlotThreadRead(ORMModel):
    id: str
    project_id: str
    name: str
    description: str | None = None
    thread_type: str
    status: str
    planted_at: str | None = None
    payoff_at: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class StoryMapResponse(BaseModel):
    project: ProjectRead
    chapters: list[ChapterRead] = Field(default_factory=list)
    canon_items: list[CanonItemRead] = Field(default_factory=list)
    timeline_events: list[TimelineEventRead] = Field(default_factory=list)
    plot_threads: list[PlotThreadRead] = Field(default_factory=list)


class GenerationRequest(BaseModel):
    chapter_id: str | None = None
    idempotency_key: str
    # Optional one-off Provider selection.  When omitted the authenticated
    # user's explicit default_provider_id is resolved before any chapter/job
    # row is created.
    provider_id: str | None = None
    # A batch is still executed one chapter at a time.  The next chapter is
    # queued only after the preceding review is accepted.
    chapter_count: int = Field(default=1, ge=1, le=10)
    target_word_count: int | None = Field(default=None, ge=1)
    instructions: str | None = None
    mode: str = "quality"


class JobState(ORMModel):
    id: str
    project_id: str
    chapter_id: str | None = None
    idempotency_key: str
    state: str
    current_stage: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AuditIssue(BaseModel):
    code: str
    severity: str
    message: str
    suggestion: str | None = None
    source_refs: list[dict[str, Any]] = Field(default_factory=list)


class CanonChange(BaseModel):
    action: str
    canon_item_id: str | None = None
    category: str = "general"
    key: str
    value: Any = None
    is_hard: bool = False
    source_revision_id: str | None = None
    source_start: int | None = None
    source_end: int | None = None
    source_excerpt: str | None = None


class ReviewBundleRead(ORMModel):
    id: str
    project_id: str
    chapter_id: str | None = None
    generation_run_id: str | None = None
    base_canon_version: int
    status: str
    draft_revision_id: str | None = None
    canon_changes: list[Any] = Field(default_factory=list)
    audit_issues: list[Any] = Field(default_factory=list)
    source_context: list[Any] = Field(default_factory=list)
    rejection_reason: str | None = None
    force_accept_reason: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class ProviderProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str | None = None
    protocol: Literal["chat_completions", "responses", "anthropic_messages"] = "chat_completions"
    api_version: str | None = Field(default=None, max_length=80)
    max_output_tokens: int | None = Field(default=None, ge=1)
    anthropic_workspace_id: str | None = Field(default=None, max_length=255)
    model_role_mapping: dict[str, Any] = Field(default_factory=dict)
    context_length: int = Field(default=8192, ge=1)
    timeout_seconds: int = Field(default=120, ge=1)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    # Legacy credential reference is metadata only; secret material never
    # belongs in this schema or in a database export.
    api_key_ref: str | None = None
    enabled: bool = True


__all__ = [name for name in globals() if not name.startswith("_")]
