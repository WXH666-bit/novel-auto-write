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
    auto_summary_enabled: bool = True
    preferences_version: int = 1
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
    start_mode: Literal["blank", "setup", "import"] = "setup"
    first_chapter_title: str | None = Field(default=None, min_length=1, max_length=255)

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
    destination: Literal["current_blank", "new_child"] | None = None
    skip_memory_once: bool = False
    skip_memory_reason: str | None = Field(default=None, max_length=1000)


class JobState(ORMModel):
    id: str
    project_id: str
    chapter_id: str | None = None
    idempotency_key: str
    kind: str = "generation"
    resource_id: str | None = None
    attempts: int = 0
    max_attempts: int = 3
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
    summary_candidate: str | None = None
    structured_candidates: dict[str, Any] = Field(default_factory=dict)
    audit_issues: list[Any] = Field(default_factory=list)
    source_context: list[Any] = Field(default_factory=list)
    rejection_reason: str | None = None
    force_accept_reason: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class PreferencesRead(ORMModel):
    auto_summary_enabled: bool = True
    preferences_version: int = 1


class PreferencesUpdate(BaseModel):
    auto_summary_enabled: bool | None = None
    expected_version: int | None = Field(default=None, ge=1)


class StorySummaryRevisionRead(ORMModel):
    id: str
    story_summary_id: str
    source_revision_id: str | None = None
    summary_text: str
    structured_json: dict[str, Any] = Field(default_factory=dict)
    provider_profile_id: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    memory_epoch: int
    created_at: datetime


class StorySummaryRead(ORMModel):
    id: str
    project_id: str
    scope: str
    chapter_id: str | None = None
    current_revision_id: str | None = None
    status: str
    summary_text: str
    structured_json: dict[str, Any] = Field(default_factory=dict)
    memory_epoch: int
    created_at: datetime
    updated_at: datetime
    revisions: list[StorySummaryRevisionRead] = Field(default_factory=list)


class StorySummaryUpsert(BaseModel):
    scope: Literal["project", "chapter", "arc"] = "project"
    chapter_id: str | None = None
    source_revision_id: str | None = None
    summary_text: str = ""
    structured_json: dict[str, Any] = Field(default_factory=dict)
    provider_profile_id: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    memory_epoch: int | None = Field(default=None, ge=0)
    expected_memory_epoch: int | None = Field(default=None, ge=0)


class MemoryBuildRunCreate(BaseModel):
    scope: Literal["project", "chapter", "arc"] = "project"
    chapter_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)
    provider_profile_id: str | None = None


class MemoryBuildRunRead(ORMModel):
    id: str
    project_id: str
    chapter_id: str | None = None
    scope: str
    status: str
    idempotency_key: str
    provider_profile_id: str | None = None
    stage: str
    resource_id: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class MemoryBuildArtifactRead(ORMModel):
    id: str
    run_id: str
    stage: str
    content_hash: str
    content: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CharacterFields(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list)
    role: str | None = Field(default=None, max_length=120)
    gender: str | None = Field(default=None, max_length=80)
    pronouns: str | None = Field(default=None, max_length=80)
    age: str | None = Field(default=None, max_length=80)
    occupation: str | None = Field(default=None, max_length=160)
    appearance: str | None = None
    personality: str | None = None
    background: str | None = None
    goals: str | None = None
    goal: str | None = None
    motivation: str | None = None
    conflict_fears: str | None = None
    conflict: str | None = None
    abilities: str | None = None
    tags: list[str] = Field(default_factory=list)
    arc: str | None = None
    voice: str | None = None
    status: str = Field(default="active", max_length=40)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    image_media_id: str | None = None


class CharacterCreate(CharacterFields):
    source_type: str = Field(default="manual", max_length=40)


class CharacterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    aliases: list[str] | None = None
    role: str | None = Field(default=None, max_length=120)
    gender: str | None = Field(default=None, max_length=80)
    pronouns: str | None = Field(default=None, max_length=80)
    age: str | None = Field(default=None, max_length=80)
    occupation: str | None = Field(default=None, max_length=160)
    appearance: str | None = None
    personality: str | None = None
    background: str | None = None
    goals: str | None = None
    goal: str | None = None
    motivation: str | None = None
    conflict_fears: str | None = None
    conflict: str | None = None
    abilities: str | None = None
    tags: list[str] | None = None
    arc: str | None = None
    voice: str | None = None
    status: str | None = Field(default=None, max_length=40)
    custom_fields: dict[str, Any] | None = None
    image_media_id: str | None = None
    source_type: str | None = Field(default=None, max_length=40)
    expected_version: int | None = Field(default=None, ge=1)


class CharacterRevisionRead(ORMModel):
    id: str
    character_id: str
    revision_number: int
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: str | None = None
    gender: str | None = None
    pronouns: str | None = None
    age: str | None = None
    occupation: str | None = None
    appearance: str | None = None
    personality: str | None = None
    background: str | None = None
    goals: str | None = None
    goal: str | None = None
    motivation: str | None = None
    conflict_fears: str | None = None
    conflict: str | None = None
    abilities: str | None = None
    tags: list[str] = Field(default_factory=list)
    arc: str | None = None
    voice: str | None = None
    status: str
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    image_media_id: str | None = None
    source_type: str
    source_revision_id: str | None = None
    created_by_user_id: str | None = None
    created_at: datetime


class CharacterRead(CharacterFields, ORMModel):
    id: str
    project_id: str
    current_revision_id: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    revisions: list[CharacterRevisionRead] = Field(default_factory=list)


class StoryGraphNodeCreate(BaseModel):
    scope_chapter_id: str | None = None
    node_type: str = Field(default="custom", min_length=1, max_length=40)
    ref_id: str | None = None
    character_id: str | None = None
    chapter_id: str | None = None
    plot_thread_id: str | None = None
    label: str = Field(default="", max_length=255)
    data: dict[str, Any] = Field(default_factory=dict)
    position_x: float | None = None
    position_y: float | None = None
    width: float | None = Field(default=None, ge=0)
    height: float | None = Field(default=None, ge=0)
    status: str = Field(default="active", max_length=40)


class StoryGraphNodeUpdate(BaseModel):
    scope_chapter_id: str | None = None
    node_type: str | None = Field(default=None, min_length=1, max_length=40)
    ref_id: str | None = None
    character_id: str | None = None
    chapter_id: str | None = None
    plot_thread_id: str | None = None
    label: str | None = Field(default=None, max_length=255)
    data: dict[str, Any] | None = None
    position_x: float | None = None
    position_y: float | None = None
    width: float | None = Field(default=None, ge=0)
    height: float | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, max_length=40)
    expected_version: int | None = Field(default=None, ge=1)


class StoryGraphNodeRead(ORMModel):
    id: str
    project_id: str
    scope_chapter_id: str | None = None
    node_type: str
    ref_id: str | None = None
    character_id: str | None = None
    chapter_id: str | None = None
    plot_thread_id: str | None = None
    label: str
    data: dict[str, Any] = Field(default_factory=dict)
    position_x: float | None = None
    position_y: float | None = None
    width: float | None = None
    height: float | None = None
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class StoryGraphEdgeCreate(BaseModel):
    scope_chapter_id: str | None = None
    source_node_id: str
    target_node_id: str
    relation_type: str = Field(default="related", min_length=1, max_length=80)
    label: str | None = Field(default=None, max_length=255)
    directed: bool = True
    weight: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="active", max_length=40)


class StoryGraphEdgeUpdate(BaseModel):
    scope_chapter_id: str | None = None
    source_node_id: str | None = None
    target_node_id: str | None = None
    relation_type: str | None = Field(default=None, min_length=1, max_length=80)
    label: str | None = Field(default=None, max_length=255)
    directed: bool | None = None
    weight: float | None = None
    data: dict[str, Any] | None = None
    status: str | None = Field(default=None, max_length=40)
    expected_version: int | None = Field(default=None, ge=1)


class StoryGraphEdgeRead(ORMModel):
    id: str
    project_id: str
    scope_chapter_id: str | None = None
    source_node_id: str
    target_node_id: str
    relation_type: str
    label: str | None = None
    directed: bool
    weight: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class StoryGraphLayoutUpdate(BaseModel):
    scope_chapter_id: str | None = None
    layout_json: dict[str, Any] = Field(default_factory=dict)
    expected_version: int | None = Field(default=None, ge=1)


class StoryGraphLayoutRead(ORMModel):
    id: str
    project_id: str
    scope_chapter_id: str | None = None
    layout_json: dict[str, Any] = Field(default_factory=dict)
    version: int
    created_at: datetime
    updated_at: datetime


class StoryGraphRead(BaseModel):
    chapter_id: str | None = None
    nodes: list[StoryGraphNodeRead] = Field(default_factory=list)
    edges: list[StoryGraphEdgeRead] = Field(default_factory=list)
    layout: StoryGraphLayoutRead | None = None


class MediaAssetRead(ORMModel):
    id: str
    project_id: str
    kind: str
    original_name: str
    mime: str
    size: int
    checksum: str
    width: int
    height: int
    alt: str | None = None
    created_at: datetime
    download_url: str | None = None


class ChangeSetRead(ORMModel):
    id: str
    project_id: str
    source_type: str
    source_id: str | None = None
    base_memory_epoch: int
    status: str
    summary: str | None = None
    changes_json: list[Any] = Field(default_factory=list)
    created_by_user_id: str | None = None
    created_at: datetime
    applied_at: datetime | None = None
    rejected_at: datetime | None = None


class ProposalRead(ORMModel):
    id: str
    project_id: str
    change_set_id: str
    operation: str
    target_type: str
    target_id: str | None = None
    scope_chapter_id: str | None = None
    patch_json: dict[str, Any] = Field(default_factory=dict)
    base_version: int | None = None
    base_memory_epoch: int | None = None
    status: str
    reason: str | None = None
    conflict_reason: str | None = None
    created_by_user_id: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class ProposalApplyRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    expected_memory_epoch: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=1000)


class ProposalPatchOperation(BaseModel):
    """One safe edit to an already persisted assistant proposal patch.

    The service deliberately treats ``path`` as a proposal-field key rather
    than as a general JSON-patch pointer.  Keeping the transport explicit is
    useful for the editor, while the server still decides which paths are
    mutable and whether their values are safe to apply.
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "replace", "remove"] = "replace"
    path: str = Field(min_length=1, max_length=255)
    value: Any = None


class ProposalUpdateRequest(BaseModel):
    """Change values in a pending/proposed assistant draft only."""

    model_config = ConfigDict(extra="forbid")

    patches: list[ProposalPatchOperation] = Field(min_length=1, max_length=50)
    # Optional optimistic-concurrency assertions.  The proposal's persisted
    # base values remain authoritative and can never be changed by this body.
    expected_version: int | None = Field(default=None, ge=1)
    expected_memory_epoch: int | None = Field(default=None, ge=0)


class ProposalRejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class ProposalBatchRequest(BaseModel):
    proposal_ids: list[str] = Field(min_length=1, max_length=100)
    expected_memory_epoch: int | None = Field(default=None, ge=0)
    expected_versions: dict[str, int] = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=1000)


class AgentConversationCreate(BaseModel):
    title: str = Field(default="故事设定助手", min_length=1, max_length=255)
    purpose: str = Field(default="setup", min_length=1, max_length=80)
    apply_mode: Literal["preview", "auto_draft"] = "preview"
    provider_profile_id: str | None = None


class AgentConversationRead(ORMModel):
    id: str
    project_id: str
    created_by_user_id: str
    title: str
    purpose: str
    apply_mode: str
    provider_profile_id: str | None = None
    # Resolved at read time from the tenant-owned profile actually selected
    # for this conversation.  Keeping these fields separate from the stored
    # snapshot lets clients display the effective provider without trusting a
    # caller-supplied fallback.
    provider_name: str | None = None
    provider_capabilities: dict[str, bool] = Field(default_factory=dict)
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class AgentMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    expected_version: int | None = Field(default=None, ge=1)
    target: dict[str, Any] = Field(default_factory=dict)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    authorized_asset_ids: list[str] = Field(default_factory=list, max_length=100)


class AgentMessageRead(ORMModel):
    id: str
    project_id: str
    conversation_id: str
    run_id: str | None = None
    sequence: int
    role: str
    content: str
    status: str
    idempotency_key: str | None = None
    request_id: str | None = None
    model_name: str | None = None
    usage_json: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    target_json: dict[str, Any] = Field(default_factory=dict)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    authorized_asset_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class AgentRunRead(ORMModel):
    id: str
    project_id: str
    conversation_id: str
    message_id: str | None = None
    job_id: str | None = None
    resource_id: str | None = None
    idempotency_key: str
    status: str
    stage: str
    provider_profile_id: str | None = None
    output_hash: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AgentEventRead(ORMModel):
    id: str
    project_id: str
    conversation_id: str
    run_id: str | None = None
    sequence: int
    event_type: str
    payload_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


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
