export type View = "library" | "desk" | "settings";
export type LedgerTab = "canon" | "timeline" | "threads" | "foreshadowing";
export type AuthView =
  | "login"
  | "register"
  | "verify-email"
  | "forgot-password"
  | "reset-password"
  | "account";
export type AuthMode = "email" | "username";
export type CanonStatus =
  | "pending"
  | "confirmed"
  | "superseded"
  | "needs_review";
export type Severity = "critical" | "major" | "minor" | "note";

export interface Project {
  id: string;
  title: string;
  logline?: string;
  genre?: string;
  viewpoint?: string;
  tone?: string;
  word_target?: number;
  chapter_target?: number;
  current_chapter_id?: string | null;
  canon_version?: number;
  memory_epoch?: number;
  needs_rebuild?: boolean;
  updated_at?: string;
  cover_mark?: string;
  source?: "local" | "imported";
}

export interface Chapter {
  id: string;
  project_id: string;
  number: number;
  title: string;
  status?:
    | "planned"
    | "draft"
    | "review"
    | "accepted"
    | "confirmed"
    | "archived";
  word_count?: number;
  summary?: string;
  content?: string;
  revision_id?: string;
  updated_at?: string;
  volume?: number;
  volume_title?: string;
}

export interface SourceRef {
  chapter_id?: string;
  chapter_title?: string;
  revision_id?: string;
  start?: number;
  end?: number;
  quote?: string;
  label?: string;
}

export interface CanonItem {
  id: string;
  project_id?: string;
  category:
    | "character"
    | "world"
    | "item"
    | "relationship"
    | "constraint"
    | "setting";
  subject: string;
  predicate?: string;
  value: string;
  status: CanonStatus;
  hard?: boolean;
  aliases?: string[];
  valid_from?: string;
  valid_to?: string;
  source_ref?: SourceRef;
  source?: SourceRef;
  note?: string;
}

export interface TimelineEvent {
  id: string;
  title: string;
  date_label?: string;
  chapter_id?: string;
  chapter_number?: number;
  status?: "past" | "current" | "planned" | "unknown";
  description?: string;
  source_ref?: SourceRef;
}

export interface PlotThread {
  id: string;
  title: string;
  kind?: "main" | "subplot" | "foreshadowing" | "relationship";
  status?: "active" | "resolved" | "dormant" | "blocked";
  color?: string;
  points?: Array<{
    chapter_number: number;
    label: string;
    state: "seed" | "advance" | "payoff";
  }>;
  next_beat?: string;
}

export interface AuditIssue {
  id: string;
  severity: Severity;
  type?: string;
  title: string;
  detail: string;
  suggestion?: string;
  source_refs?: SourceRef[];
  resolved?: boolean;
}

export interface CanonChange {
  id: string;
  action: "create" | "update" | "supersede" | "review";
  item: CanonItem;
  before?: CanonItem | null;
  reason?: string;
  source_ref?: SourceRef;
}

export interface ReviewBundle {
  id: string;
  project_id: string;
  chapter_id: string;
  revision_id?: string;
  status:
    | "awaiting_review"
    | "accepted"
    | "rejected"
    | "force_accepted"
    | "stale";
  issues: AuditIssue[];
  canon_changes: CanonChange[];
  source_context?: SourceRef[];
  generated_at?: string;
  content_snapshot?: string;
  blocking_count?: number;
}

export type JobStatus =
  | "queued"
  | "preparing_context"
  | "planning"
  | "drafting"
  | "extracting"
  | "auditing"
  | "revising"
  | "awaiting_review"
  | "committing"
  | "completed"
  | "failed"
  | "cancelled"
  | "needs_retry";

export interface GenerationJob {
  id: string;
  project_id: string;
  chapter_id?: string;
  status: JobStatus;
  progress?: number;
  phase_label?: string;
  created_at?: string;
  error?: string;
  provider_name?: string;
  provider_id?: string;
  review_bundle_id?: string;
}

export interface ProviderProfile {
  id?: string;
  name: string;
  base_url: string;
  protocol?: "chat_completions" | "responses" | "anthropic_messages";
  api_version?: string;
  max_output_tokens?: number;
  anthropic_workspace_id?: string;
  default_model?: string;
  model_roles?: Record<string, string>;
  context_length?: number;
  timeout_ms?: number;
  capabilities?: Record<string, boolean>;
  enabled?: boolean;
  deleted_at?: string | null;
  api_key_set?: boolean;
  api_key?: string;
}

export interface User {
  id: string;
  email?: string;
  username?: string;
  display_name?: string;
  is_email_verified: boolean;
  is_active?: boolean;
  default_provider_id?: string | null;
  created_at?: string;
}

export interface AuthSession {
  user: User;
  csrf_token?: string;
}

export interface AuthConfig {
  mode: AuthMode;
  verification_required: boolean;
  password_reset_available: boolean;
}

export interface ImportChapterPreview {
  key: string;
  number: number;
  title: string;
  content: string;
  selected: boolean;
  source_start?: number;
  source_end?: number;
}

export interface ImportPreview {
  file_name: string;
  file_hash?: string;
  source_hash?: string;
  encoding?: string;
  chapters: ImportChapterPreview[];
  source_text?: string;
  warnings?: string[];
}

export interface StoryMap {
  threads?: PlotThread[];
  timeline?: TimelineEvent[];
  characters?: CanonItem[];
  foreshadowing?: CanonItem[];
}
