export type View = "library" | "desk" | "studio" | "settings";
export type LedgerTab = "canon" | "timeline" | "threads" | "foreshadowing";
export type StartMode = "blank" | "import" | "setup";
export type StudioMode = "manuscript" | "characters" | "story-map";
export type EntityViewMode = "table" | "graph";
export type SummaryStatus =
  | "not_started"
  | "unprocessed"
  | "queued"
  | "running"
  | "current"
  | "needs_review"
  | "stale"
  | "failed";
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
  style?: string;
  story_bible?: string;
  outline?: Record<string, unknown>;
  word_target?: number;
  target_word_count?: number;
  chapter_target?: number;
  must_happen?: string[];
  must_not_happen?: string[];
  current_chapter_id?: string | null;
  canon_version?: number;
  memory_epoch?: number;
  needs_rebuild?: boolean;
  updated_at?: string;
  cover_mark?: string;
  source?: "local" | "imported";
  summary_status?: SummaryStatus;
}

export interface Chapter {
  id: string;
  project_id: string;
  number: number;
  title: string;
  status?:
    | "planned"
    | "draft"
    | "queued"
    | "generating"
    | "failed"
    | "rejected"
    | "review"
    | "accepted"
    | "confirmed"
    | "archived";
  word_count?: number;
  summary?: string;
  summary_status?: SummaryStatus;
  content?: string;
  revision_id?: string;
  updated_at?: string;
  volume?: number;
  volume_title?: string;
}

export interface AccountPreferences {
  auto_summary_enabled: boolean;
  default_start_mode?: StartMode;
  preferences_version?: number;
  updated_at?: string;
}

export interface PortraitAsset {
  id: string;
  project_id: string;
  filename?: string;
  url: string;
  alt?: string;
  width?: number;
  height?: number;
  content_type?: string;
  byte_size?: number;
  checksum?: string;
  created_at?: string;
}

export type CharacterStatus =
  | "draft"
  | "pending"
  | "confirmed"
  | "needs_review"
  | "archived"
  | "active";

export interface CharacterCard {
  id: string;
  project_id: string;
  name: string;
  aliases: string[];
  role?: string;
  age?: string;
  gender?: string;
  pronouns?: string;
  occupation?: string;
  appearance?: string;
  personality?: string;
  background?: string;
  goals?: string;
  motivation?: string;
  conflict_fears?: string;
  abilities?: string;
  arc?: string;
  voice?: string;
  tags: string[];
  custom_fields: Record<string, string>;
  portrait?: PortraitAsset | null;
  status: CharacterStatus;
  image_media_id?: string | null;
  source_refs: SourceRef[];
  canon_item_id?: string;
  current_revision_id?: string | null;
  version?: number;
  updated_at?: string;
  created_at?: string;
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
    | "setting"
    | "general";
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

export type StoryGraphNodeType = "character" | "thread" | "event";

export interface StoryGraphNode {
  id: string;
  type: StoryGraphNodeType;
  label: string;
  subtitle?: string;
  image_url?: string;
  status?: string;
  position: { x: number; y: number };
  data?: Record<string, unknown>;
  scope_chapter_id?: string | null;
  ref_id?: string | null;
  character_id?: string | null;
  chapter_id?: string | null;
  plot_thread_id?: string | null;
  source_refs?: SourceRef[];
  version?: number;
}

export interface StoryGraphEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  kind?: string;
  direction?: "directed" | "undirected";
  status?: "active" | "draft" | "pending" | "confirmed" | "needs_review";
  note?: string;
  source_refs?: SourceRef[];
  relation_type?: string;
  directed?: boolean;
  weight?: number;
  data?: Record<string, unknown>;
  scope_chapter_id?: string | null;
  source_node_id?: string;
  target_node_id?: string;
  version?: number;
}

export interface StoryGraph {
  chapter_id?: string | null;
  nodes: StoryGraphNode[];
  edges: StoryGraphEdge[];
  version?: number;
  layout_version?: number;
  updated_at?: string;
}

/**
 * Memory runs are derived snapshots.  `current` is the terminal successful
 * state returned by the API; `stale` means the source revision moved while a
 * run was in flight.  Keep `completed`/`needs_retry` as compatibility values
 * for older deployments, but prefer the canonical states in new UI code.
 */
export type MemoryRunStatus =
  | "queued"
  | "running"
  | "current"
  | "failed"
  | "stale"
  | "skipped"
  | "cancelled"
  | "completed"
  | "needs_retry";

export interface MemoryRun {
  id: string;
  project_id: string;
  status: MemoryRunStatus;
  scope?: "project" | "chapter" | "arc" | "chapters";
  chapter_id?: string | null;
  chapter_ids?: string[];
  progress?: number;
  phase_label?: string;
  error?: string;
  created_at?: string;
  completed_at?: string;
  stage?: string;
  started_at?: string;
  finished_at?: string;
  idempotency_key?: string;
  provider_profile_id?: string | null;
}

export interface StorySummary {
  id: string;
  project_id: string;
  scope: string;
  chapter_id?: string | null;
  current_revision_id?: string | null;
  status: string;
  summary_text: string;
  structured_json: Record<string, unknown>;
  memory_epoch: number;
  created_at?: string;
  updated_at?: string;
}

export interface ProjectMemory {
  project_id: string;
  memory_epoch: number;
  auto_summary_enabled: boolean;
  project_summary: StorySummary | null;
  chapter_summaries: StorySummary[];
  runs: MemoryRun[];
}

export type MemoryRunEvent =
  | { type: "progress"; run: MemoryRun }
  | {
      type: "artifact";
      sequence: number;
      stage?: string;
      content_hash?: string;
    };

export type AgentTarget =
  | { type: "project"; id: string; chapter_id?: string | null }
  | { type: "character"; id: string; chapter_id?: string | null }
  | { type: "thread"; id: string; chapter_id?: string | null }
  | { type: "relationship"; id: string; chapter_id?: string | null }
  | { type: "chapter"; id: string; chapter_id?: string | null };

export type AgentRunStatus =
  | "idle"
  | "queued"
  | "running"
  | "streaming"
  | "reconnecting"
  | "applying"
  | "applied"
  | "error"
  | "disconnected"
  | "cancelled";

export interface AgentPatch {
  path: string;
  value: unknown;
  label?: string;
  source_refs?: SourceRef[];
  confidence?: number;
}

export interface AssistantProposalUpdatePatch {
  op: "add" | "replace" | "remove";
  path: string;
  value?: unknown;
}

export interface AssistantProposalActionDetail {
  projectId?: string;
  proposalId: string;
  action: "apply" | "reject";
  patches?: AssistantProposalUpdatePatch[];
}

export interface AgentSelectionSnapshot {
  chapter_id: string;
  base_revision_id?: string | null;
  start: number;
  end: number;
  hash: string;
  quote?: string;
}

export interface AgentContextSnapshot {
  chapter_id?: string | null;
  base_revision_id?: string | null;
  selection?: AgentSelectionSnapshot | null;
  selection_start?: number;
  selection_end?: number;
  selection_hash?: string;
  selected_text?: string;
  [key: string]: unknown;
}

export type AssistantProposalStatus =
  | "building"
  | "proposed"
  | "applying"
  | "applied"
  | "rejected"
  | "conflict"
  | "stale";

export interface AssistantProposal {
  id: string;
  conversation_id: string;
  target: AgentTarget;
  summary: string;
  patches: AgentPatch[];
  status: AssistantProposalStatus;
  created_at?: string;
  operation?: string;
  target_type?: string;
  target_id?: string | null;
  scope_chapter_id?: string | null;
  change_set_id?: string;
  base_version?: number | null;
  base_memory_epoch?: number | null;
  reason?: string;
}

export interface AssistantMessage {
  id: string;
  run_id?: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
  proposal_ids?: string[];
  status?: string;
  target?: AgentTarget;
  context_snapshot?: AgentContextSnapshot;
  authorized_asset_ids?: string[];
}

export interface AssistantConversation {
  id: string;
  project_id: string;
  target: AgentTarget;
  status: AgentRunStatus;
  messages: AssistantMessage[];
  proposals: AssistantProposal[];
  title?: string;
  purpose?: string;
  version?: number;
  provider_profile_id?: string | null;
  provider_name?: string;
  provider_capabilities?: Record<string, boolean>;
  updated_at?: string;
}

export interface AssistantRun {
  id: string;
  project_id: string;
  conversation_id: string;
  message_id?: string | null;
  status: string;
  stage?: string;
  provider_profile_id?: string | null;
  error?: string | null;
  attempt?: number;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
}

interface AssistantEventMeta {
  sequence: number;
  run_id?: string;
  attempt?: number;
  target?: AgentTarget;
  base_version?: number | null;
  cursor?: string;
  retryable?: boolean;
}

export type AssistantEvent =
  | (AssistantEventMeta & {
      type: "message_delta";
      message_id: string;
      delta: string;
    })
  | (AssistantEventMeta & {
      type: "message_replace";
      message_id: string;
      content: string;
    })
  | (AssistantEventMeta & {
      type: "message_completed";
      message_id: string;
      reply: string;
      proposal_count?: number;
    })
  | (AssistantEventMeta & {
      type: "proposal_created";
      proposal: AssistantProposal;
    })
  | (AssistantEventMeta & {
      type: "proposal_patch";
      proposal_id: string;
      patch: AgentPatch;
    })
  | (AssistantEventMeta & { type: "proposal_completed"; proposal_id: string })
  | (AssistantEventMeta & {
      type: "status";
      status: AgentRunStatus;
      stage?: string;
      message?: string;
    })
  | (AssistantEventMeta & { type: "error"; message: string });

export type AttentionKind = "review" | "recheck" | "proposal" | "retry";

export interface ProjectAttentionItem {
  id: string;
  kind: AttentionKind;
  status: string;
  title: string;
  detail?: string;
  chapter_id?: string | null;
  conversation_id?: string | null;
  run_id?: string | null;
  task_type?: "generation" | "memory" | "assistant" | "review" | string;
  job_id?: string | null;
  target_type?: string | null;
  created_at?: string;
}

export interface ProjectAttention {
  total: number;
  reviews: number;
  rechecks: number;
  proposals: number;
  retries: number;
  items: ProjectAttentionItem[];
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
  | "running"
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
  chapter_count?: number;
  chapter_index?: number;
  batch_index?: number;
  batch_total?: number;
  batch_remaining?: number;
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
  is_default?: boolean;
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
  graph?: StoryGraph;
}
