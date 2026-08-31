import { demoProvider } from "./demoData";
import type {
  CanonItem,
  Chapter,
  GenerationJob,
  ImportPreview,
  PlotThread,
  Project,
  ProviderProfile,
  ReviewBundle,
  SourceRef,
  StoryMap,
  TimelineEvent,
} from "./types";

const API_ROOT = (import.meta.env.VITE_API_BASE_URL || "/api").replace(
  /\/$/,
  "",
);

function unwrap<T>(payload: unknown, keys: string[] = []): T {
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    for (const key of keys) {
      if (record[key] !== undefined) return record[key] as T;
    }
    if (record.data !== undefined) return record.data as T;
  }
  return payload as T;
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  if (!response.ok) {
    let detail = "";
    try {
      const raw = await response.text();
      try {
        detail = String(
          (JSON.parse(raw) as { detail?: unknown }).detail ?? raw,
        );
      } catch {
        detail = raw;
      }
    } catch {
      /* server may close early */
    }
    const error = new Error(detail || `请求失败（${response.status}）`);
    (error as Error & { status?: number }).status = response.status;
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function errorStatus(error: unknown) {
  return (error as Error & { status?: number })?.status;
}

export async function getProjects(): Promise<Project[]> {
  const payload = await apiRequest<unknown>("/projects");
  const result = unwrap<unknown>(payload, ["projects", "items"]);
  return (Array.isArray(result) ? result : []).map(normalizeProject);
}

export async function getProject(projectId: string): Promise<Project> {
  return normalizeProject(await apiRequest<unknown>(`/projects/${projectId}`));
}

export async function createProject(input: Partial<Project>): Promise<Project> {
  const payload = await apiRequest<unknown>("/projects", {
    method: "POST",
    body: JSON.stringify({
      ...input,
      name: input.title,
      description: input.logline,
      target_word_count: input.word_target,
    }),
  });
  return normalizeProject(payload);
}

export async function updateProject(
  projectId: string,
  input: Partial<Project>,
): Promise<Project> {
  const payload = await apiRequest<unknown>(`/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify({
      ...input,
      ...(input.title !== undefined ? { name: input.title } : {}),
      ...(input.logline !== undefined ? { description: input.logline } : {}),
      ...(input.word_target !== undefined
        ? { target_word_count: input.word_target }
        : {}),
    }),
  });
  return normalizeProject(payload);
}

export async function getChapters(projectId: string): Promise<Chapter[]> {
  const payload = await apiRequest<unknown>(`/projects/${projectId}/chapters`);
  const result = unwrap<unknown>(payload, ["chapters", "items"]);
  const chapters = Array.isArray(result) ? result : [];
  return chapters.map(normalizeChapter).sort((a, b) => a.number - b.number);
}

export async function updateChapter(
  chapterId: string,
  input: Partial<Chapter>,
): Promise<Chapter> {
  const payload = await apiRequest<unknown>(`/chapters/${chapterId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
  return normalizeChapter(payload);
}

export async function getCanon(projectId: string): Promise<CanonItem[]> {
  const payload = await apiRequest<unknown>(`/projects/${projectId}/canon`);
  const result = unwrap<unknown>(payload, ["canon", "canon_items", "items"]);
  return (Array.isArray(result) ? result : []).map(normalizeCanon);
}

export async function getStoryMap(projectId: string): Promise<StoryMap> {
  const payload = await apiRequest<unknown>(`/projects/${projectId}/story-map`);
  const raw =
    unwrap<Record<string, unknown>>(payload, ["story_map", "storyMap"]) || {};
  const canonRows = Array.isArray(raw.canon_items) ? raw.canon_items : [];
  const canonItems = canonRows.map(normalizeCanon);
  const chapterRows = Array.isArray(raw.chapters)
    ? raw.chapters.map(normalizeChapter)
    : [];
  const chapterNumbers = new Map(
    chapterRows.map((chapter) => [chapter.id, chapter.number]),
  );
  const threadRows = Array.isArray(raw.threads ?? raw.plot_threads)
    ? ((raw.threads ?? raw.plot_threads) as unknown[])
    : [];
  const timelineRows = Array.isArray(raw.timeline ?? raw.timeline_events)
    ? ((raw.timeline ?? raw.timeline_events) as unknown[])
    : [];
  return {
    threads: threadRows.map(normalizePlotThread),
    timeline: timelineRows.map((item) =>
      normalizeTimeline(item, chapterNumbers),
    ),
    characters: canonItems.filter((item) => item.category === "character"),
    foreshadowing: canonItems.filter(
      (item) => item.category === "constraint" || item.category === "item",
    ),
  };
}

export async function createGeneration(
  projectId: string,
  input: Record<string, unknown>,
): Promise<GenerationJob> {
  const instructions = [
    input.instructions,
    input.must ? `必须发生：${String(input.must)}` : "",
    input.must_not ? `禁止发生：${String(input.must_not)}` : "",
  ]
    .filter(Boolean)
    .join("\n");
  const body = {
    chapter_id: input.chapter_id || null,
    idempotency_key: input.idempotency_key || crypto.randomUUID(),
    target_word_count: Number(
      input.target_word_count ?? input.word_target ?? 3500,
    ),
    instructions,
    mode:
      input.mode === "outline"
        ? "outline"
        : input.mode === "rewrite"
          ? "rewrite"
          : "quality",
  };
  const payload = await apiRequest<unknown>(
    `/projects/${projectId}/generations`,
    { method: "POST", body: JSON.stringify(body) },
  );
  return normalizeJob(payload);
}

export async function getGeneration(jobId: string): Promise<GenerationJob> {
  const payload = await apiRequest<unknown>(`/generations/${jobId}`);
  return normalizeJob(payload);
}

export async function getLatestGeneration(
  projectId: string,
): Promise<GenerationJob | null> {
  try {
    return normalizeJob(
      await apiRequest<unknown>(`/projects/${projectId}/generations/latest`),
    );
  } catch (error) {
    if (errorStatus(error) === 404) return null;
    throw error;
  }
}

export async function retryGeneration(jobId: string): Promise<GenerationJob> {
  return normalizeJob(
    await apiRequest<unknown>(`/generations/${jobId}/retry`, {
      method: "POST",
    }),
  );
}

export function generationEventsUrl(jobId: string) {
  return `${API_ROOT}/generations/${jobId}/events`;
}

export function listenGenerationEvents(
  jobId: string,
  onJob: (job: GenerationJob) => void,
  onError?: () => void,
) {
  const source = new EventSource(generationEventsUrl(jobId));
  const handleProgress = (event: MessageEvent<string>) => {
    try {
      onJob(normalizeJob(JSON.parse(event.data)));
    } catch {
      /* malformed progress events are ignored */
    }
  };
  source.onmessage = handleProgress;
  source.addEventListener("progress", handleProgress as EventListener);
  source.onerror = () => {
    source.close();
    onError?.();
  };
  return () => source.close();
}

export async function getReview(reviewId: string): Promise<ReviewBundle> {
  return normalizeReview(await apiRequest<unknown>(`/reviews/${reviewId}`));
}

export async function editReviewDraft(
  reviewId: string,
  content: string,
): Promise<ReviewBundle> {
  const payload = await apiRequest<unknown>(`/reviews/${reviewId}/draft`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
  return normalizeReview(payload);
}

export async function reviewAction(
  reviewId: string,
  action: "reaudit" | "accept" | "reject",
  input: Record<string, unknown> = {},
): Promise<ReviewBundle> {
  const body =
    action === "accept"
      ? {
          force_reason: input.force
            ? String(input.reason || "")
            : input.force_reason,
        }
      : action === "reject"
        ? { reason: String(input.reason || "用户拒绝此审核包") }
        : input;
  const payload = await apiRequest<unknown>(`/reviews/${reviewId}/${action}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  return normalizeReview(payload);
}

export async function getDefaultProvider(): Promise<ProviderProfile> {
  try {
    return normalizeProvider(await apiRequest<unknown>("/providers/default"));
  } catch (error) {
    if (errorStatus(error) === 404) return demoProvider;
    throw error;
  }
}

export async function rebuildProjectMemory(
  projectId: string,
): Promise<Project> {
  return normalizeProject(
    await apiRequest<unknown>(`/projects/${projectId}/memory/rebuild`, {
      method: "POST",
    }),
  );
}

export async function putDefaultProvider(
  input: ProviderProfile,
): Promise<ProviderProfile> {
  const payload = await apiRequest<unknown>("/providers/default", {
    method: "PUT",
    body: JSON.stringify(input),
  });
  return normalizeProvider(payload);
}

export async function testProvider(
  input: Partial<ProviderProfile>,
): Promise<{
  ok: boolean;
  latency_ms?: number;
  model?: string;
  message?: string;
}> {
  const payload = await apiRequest<unknown>("/providers/test", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return unwrap(payload, ["result", "data"]) as {
    ok: boolean;
    latency_ms?: number;
    model?: string;
    message?: string;
  };
}

export async function previewImport(
  projectId: string,
  file: File,
): Promise<ImportPreview> {
  const form = new FormData();
  form.append("file", file);
  const payload = unwrap<Record<string, unknown>>(
    await apiRequest<unknown>(`/projects/${projectId}/import/preview`, {
      method: "POST",
      body: form,
    }),
    ["preview", "data"],
  );
  const chapters = Array.isArray(payload.chapters)
    ? (payload.chapters as Array<Record<string, unknown>>)
    : [];
  return {
    file_name: String(payload.file_name ?? payload.filename ?? file.name),
    file_hash: String(payload.file_hash ?? payload.source_hash ?? ""),
    source_hash: String(payload.source_hash ?? payload.file_hash ?? ""),
    encoding: String(payload.encoding ?? "UTF-8"),
    warnings: Array.isArray(payload.warnings)
      ? payload.warnings.map(String)
      : [],
    chapters: chapters.map((chapter, index) => ({
      key: String(chapter.key ?? chapter.content_hash ?? `import-${index}`),
      number: Number(chapter.number ?? chapter.ordinal ?? index + 1),
      title: String(chapter.title ?? `第 ${index + 1} 章`),
      content: String(chapter.content ?? ""),
      selected: chapter.selected !== false,
      source_start: Number(chapter.source_start ?? 0),
      source_end: Number(
        chapter.source_end ?? String(chapter.content ?? "").length,
      ),
    })),
  };
}

export async function commitImport(
  projectId: string,
  input: ImportPreview,
): Promise<Chapter[]> {
  const body = {
    filename: input.file_name,
    source_hash: input.source_hash || input.file_hash,
    encoding: input.encoding,
    chapters: input.chapters
      .filter((chapter) => chapter.selected)
      .map((chapter, index) => ({
        ordinal: chapter.number || index + 1,
        title: chapter.title,
        content: chapter.content,
        source_start: chapter.source_start || 0,
        source_end: chapter.source_end || chapter.content.length,
        source_type: "import",
      })),
  };
  const payload = await apiRequest<unknown>(
    `/projects/${projectId}/import/commit`,
    { method: "POST", body: JSON.stringify(body) },
  );
  const result = unwrap<unknown>(payload, ["chapters", "items"]);
  return (Array.isArray(result) ? result : []).map(normalizeChapter);
}

export async function downloadExport(projectId: string): Promise<Blob> {
  const response = await fetch(`${API_ROOT}/projects/${projectId}/export`);
  if (!response.ok) throw new Error(`导出失败（${response.status}）`);
  return response.blob();
}

export function normalizeProject(value: unknown): Project {
  const source = (value || {}) as Record<string, unknown>;
  return {
    id: String(source.id ?? source.project_id ?? crypto.randomUUID()),
    title: String(source.title ?? source.name ?? "未命名项目"),
    logline: String(
      source.logline ?? source.summary ?? source.description ?? "",
    ),
    genre: String(source.genre ?? source.category ?? ""),
    viewpoint: String(source.viewpoint ?? source.pov ?? ""),
    tone: String(source.tone ?? source.style ?? ""),
    word_target: numberOrUndefined(
      source.word_target ??
        source.wordTarget ??
        source.target_word_count ??
        source.targetWordCount,
    ),
    chapter_target: numberOrUndefined(
      source.chapter_target ?? source.chapterTarget,
    ),
    current_chapter_id: (source.current_chapter_id ??
      source.currentChapterId ??
      source.current_chapter ??
      null) as string | null,
    canon_version: numberOrUndefined(
      source.canon_version ?? source.canonVersion,
    ),
    memory_epoch: numberOrUndefined(source.memory_epoch ?? source.memoryEpoch),
    needs_rebuild: Boolean(
      source.needs_rebuild ?? source.needsRebuild ?? false,
    ),
    updated_at: String(source.updated_at ?? source.updatedAt ?? ""),
    cover_mark: String(source.cover_mark ?? source.coverMark ?? "章"),
    source: source.source === "imported" ? "imported" : "local",
  };
}

export function normalizeChapter(value: unknown): Chapter {
  const source = (value || {}) as Record<string, unknown>;
  const revisions = Array.isArray(source.revisions)
    ? (source.revisions as Array<Record<string, unknown>>)
    : [];
  const currentRevisionId =
    source.current_revision_id ??
    source.currentRevisionId ??
    source.revision_id;
  const embeddedRevision =
    source.current_revision && typeof source.current_revision === "object"
      ? (source.current_revision as Record<string, unknown>)
      : {};
  const currentRevision =
    revisions.find((revision) => revision.id === currentRevisionId) ||
    revisions[revisions.length - 1] ||
    embeddedRevision;
  const content = String(
    source.content ??
      source.body ??
      source.text ??
      currentRevision.content ??
      currentRevision.body ??
      currentRevision.text ??
      "",
  );
  return {
    id: String(source.id ?? source.chapter_id ?? crypto.randomUUID()),
    project_id: String(source.project_id ?? source.projectId ?? ""),
    number: Number(
      source.number ??
        source.chapter_number ??
        source.chapterNumber ??
        source.sort_order ??
        0,
    ),
    title: String(
      source.title ??
        source.name ??
        `第 ${source.number ?? source.chapter_number ?? ""} 章`,
    ),
    status: (source.status ?? "draft") as Chapter["status"],
    word_count: Number(source.word_count ?? source.wordCount ?? content.length),
    summary: String(source.summary ?? source.abstract ?? ""),
    content,
    revision_id: String(currentRevisionId ?? "") || undefined,
    updated_at: String(source.updated_at ?? source.updatedAt ?? ""),
    volume: numberOrUndefined(source.volume ?? source.volume_number),
    volume_title: String(source.volume_title ?? source.volumeTitle ?? ""),
  };
}

export function normalizeCanon(value: unknown): CanonItem {
  const source = (value || {}) as Record<string, unknown>;
  const sourceRef = (source.source_ref ??
    source.sourceRef ??
    source.source ?? {
      chapter_id: source.source_chapter_id ?? source.sourceChapterId,
      revision_id: source.source_revision_id ?? source.sourceRevisionId,
      start: source.source_start ?? source.start,
      end: source.source_end ?? source.end,
      quote: source.source_quote ?? source.source_excerpt ?? source.quote,
    }) as CanonItem["source_ref"];
  const rawValue =
    source.value_text ?? source.value ?? source.description ?? "";
  return {
    ...source,
    id: String(source.id ?? source.canon_id ?? crypto.randomUUID()),
    category: (source.category ??
      source.type ??
      "setting") as CanonItem["category"],
    subject: String(source.subject ?? source.key ?? source.name ?? ""),
    predicate: String(source.predicate ?? source.field ?? ""),
    value: typeof rawValue === "string" ? rawValue : JSON.stringify(rawValue),
    status: (source.status ?? "confirmed") as CanonItem["status"],
    hard: Boolean(source.hard ?? source.is_hard ?? false),
    aliases: Array.isArray(source.aliases) ? source.aliases.map(String) : [],
    source_ref: sourceRef,
  };
}

export function normalizeJob(value: unknown): GenerationJob {
  const source = (value || {}) as Record<string, unknown>;
  const status = String(
    source.status ?? source.stage ?? "queued",
  ) as GenerationJob["status"];
  const stages: GenerationJob["status"][] = [
    "queued",
    "preparing_context",
    "planning",
    "drafting",
    "extracting",
    "auditing",
    "revising",
    "awaiting_review",
    "committing",
    "completed",
  ];
  const labels: Record<string, string> = {
    queued: "排队中",
    preparing_context: "准备上下文",
    planning: "规划场景",
    drafting: "生成正文",
    extracting: "提取事实",
    auditing: "一致性审查",
    revising: "定向修订",
    awaiting_review: "等待审核",
    committing: "提交正典",
    completed: "已完成",
    failed: "生成失败",
    needs_retry: "需要重试",
  };
  const derivedProgress =
    (Math.max(0, stages.indexOf(status)) / (stages.length - 1)) * 100;
  return {
    id: String(source.id ?? source.job_id ?? crypto.randomUUID()),
    project_id: String(source.project_id ?? source.projectId ?? ""),
    chapter_id: source.chapter_id as string | undefined,
    status,
    progress: Number(source.progress ?? source.percent ?? derivedProgress),
    phase_label: String(
      source.phase_label ??
        source.phaseLabel ??
        source.message ??
        labels[status] ??
        "",
    ),
    created_at: String(source.created_at ?? source.createdAt ?? ""),
    error: source.error as string | undefined,
    provider_name: String(source.provider_name ?? source.providerName ?? ""),
    is_demo: Boolean(source.is_demo ?? source.isDemo ?? false),
    review_bundle_id:
      String(source.review_bundle_id ?? source.reviewBundleId ?? "") ||
      undefined,
  };
}

export function normalizeReview(value: unknown): ReviewBundle {
  const source = (value || {}) as Record<string, unknown>;
  const rawIssues = Array.isArray(source.issues ?? source.audit_issues)
    ? ((source.issues ?? source.audit_issues) as Array<Record<string, unknown>>)
    : [];
  const issues = rawIssues.map((issue, index) => {
    const rawSeverity = String(issue.severity ?? "minor").toLowerCase();
    const severity = (
      ["blocker", "critical", "fatal", "high"].includes(rawSeverity)
        ? "critical"
        : rawSeverity === "error" || rawSeverity === "major"
          ? "major"
          : rawSeverity === "note"
            ? "note"
            : "minor"
    ) as ReviewBundle["issues"][number]["severity"];
    return {
      id: String(issue.id ?? issue.code ?? `issue-${index}`),
      severity,
      type: String(issue.type ?? issue.code ?? ""),
      title: String(issue.title ?? issue.code ?? "连续性问题"),
      detail: String(issue.detail ?? issue.message ?? ""),
      suggestion: String(issue.suggestion ?? ""),
      source_refs: Array.isArray(issue.source_refs)
        ? issue.source_refs.map(normalizeSourceRef)
        : [],
      resolved: Boolean(issue.resolved),
    };
  });
  const rawChanges = Array.isArray(source.canon_changes ?? source.canonChanges)
    ? ((source.canon_changes ?? source.canonChanges) as Array<
        Record<string, unknown>
      >)
    : [];
  const canonChanges = rawChanges.map((change, index) => ({
    id: String(change.id ?? `change-${index}`),
    action: (change.action ??
      "create") as ReviewBundle["canon_changes"][number]["action"],
    item: normalizeCanon({
      ...change,
      id: change.canon_item_id ?? `pending-${index}`,
      subject: change.key,
    }),
    reason: String(change.reason ?? ""),
    source_ref: normalizeSourceRef(change),
  }));
  const rawStatus = String(source.status ?? "pending");
  const status = (
    rawStatus === "pending"
      ? "awaiting_review"
      : rawStatus === "needs_review"
        ? "stale"
        : rawStatus === "force_accepted"
          ? "force_accepted"
          : rawStatus
  ) as ReviewBundle["status"];
  const blockingCount = issues.filter(
    (issue) => issue.severity === "critical",
  ).length;
  const sourceRows = Array.isArray(
    source.source_context ?? source.sourceContext,
  )
    ? ((source.source_context ?? source.sourceContext) as unknown[])
    : [];
  return {
    id: String(source.id ?? source.review_id ?? crypto.randomUUID()),
    project_id: String(source.project_id ?? source.projectId ?? ""),
    chapter_id: String(source.chapter_id ?? source.chapterId ?? ""),
    revision_id:
      String(source.revision_id ?? source.draft_revision_id ?? "") || undefined,
    status,
    issues,
    canon_changes: canonChanges,
    source_context: sourceRows.map(normalizeSourceRef),
    generated_at: String(source.generated_at ?? source.created_at ?? ""),
    content_snapshot: source.content_snapshot as string | undefined,
    blocking_count: Number(
      source.blocking_count ?? source.blockingCount ?? blockingCount,
    ),
  };
}

function normalizeSourceRef(value: unknown): SourceRef {
  const source = (value || {}) as Record<string, unknown>;
  const kind = String(source.kind ?? "");
  return {
    chapter_id:
      String(
        source.chapter_id ??
          (kind === "chapter" ? (source.source_id ?? "") : ""),
      ) || undefined,
    chapter_title:
      String(source.chapter_title ?? source.label ?? "") || undefined,
    revision_id:
      String(
        source.revision_id ??
          (kind === "search" ? (source.source_id ?? "") : ""),
      ) || undefined,
    start: numberOrUndefined(source.start ?? source.source_start),
    end: numberOrUndefined(source.end ?? source.source_end),
    quote:
      String(source.quote ?? source.excerpt ?? source.source_excerpt ?? "") ||
      undefined,
    label: String(source.label ?? source.kind ?? "") || undefined,
  };
}

function normalizePlotThread(value: unknown): PlotThread {
  const source = (value || {}) as Record<string, unknown>;
  const extra =
    source.extra && typeof source.extra === "object"
      ? (source.extra as Record<string, unknown>)
      : {};
  const rawPoints = Array.isArray(source.points ?? extra.points)
    ? ((source.points ?? extra.points) as Array<Record<string, unknown>>)
    : [];
  return {
    id: String(source.id ?? crypto.randomUUID()),
    title: String(source.title ?? source.name ?? "未命名剧情线"),
    kind: (source.kind ?? source.thread_type ?? "main") as PlotThread["kind"],
    status: (source.status ?? "active") as PlotThread["status"],
    color: String(source.color ?? extra.color ?? "#2E7D8C"),
    next_beat: String(source.next_beat ?? extra.next_beat ?? ""),
    points: rawPoints.map((point) => ({
      chapter_number: Number(point.chapter_number ?? point.chapter ?? 0),
      label: String(point.label ?? ""),
      state: (point.state ?? "advance") as "seed" | "advance" | "payoff",
    })),
  };
}

function normalizeTimeline(
  value: unknown,
  chapterNumbers: Map<string, number>,
): TimelineEvent {
  const source = (value || {}) as Record<string, unknown>;
  const chapterId = String(source.chapter_id ?? "") || undefined;
  const rawStatus = String(source.status ?? "confirmed");
  return {
    id: String(source.id ?? crypto.randomUUID()),
    title: String(source.title ?? "未命名事件"),
    date_label: String(source.date_label ?? source.story_time ?? "时间未定"),
    chapter_id: chapterId,
    chapter_number:
      numberOrUndefined(source.chapter_number) ??
      (chapterId ? chapterNumbers.get(chapterId) : undefined),
    status:
      rawStatus === "planned"
        ? "planned"
        : rawStatus === "current"
          ? "current"
          : "past",
    description: String(source.description ?? ""),
    source_ref: normalizeSourceRef(source),
  };
}

export function normalizeProvider(value: unknown): ProviderProfile {
  const source = (value || {}) as Record<string, unknown>;
  const roles = (source.model_roles ??
    source.modelRoles ??
    source.model_role_mapping ??
    {}) as Record<string, string>;
  return {
    id: String(source.id ?? "") || undefined,
    name: String(source.name ?? "未命名 Provider"),
    base_url: String(source.base_url ?? source.baseUrl ?? ""),
    protocol: (source.protocol ??
      source.protocol_mode ??
      "chat_completions") as ProviderProfile["protocol"],
    default_model: String(
      source.default_model ??
        source.model ??
        roles.default ??
        roles.writer ??
        "",
    ),
    model_roles: roles,
    context_length: Number(
      source.context_length ?? source.contextLength ?? 32768,
    ),
    timeout_ms: Number(
      source.timeout_ms ??
        source.timeoutMs ??
        Number(source.timeout_seconds ?? 60) * 1000,
    ),
    capabilities: (source.capabilities ?? {}) as Record<string, boolean>,
    is_demo: Boolean(source.is_demo ?? source.isDemo),
    api_key_set: Boolean(
      source.api_key_set ?? source.apiKeySet ?? source.has_api_key,
    ),
  };
}

function numberOrUndefined(value: unknown): number | undefined {
  const parsed = Number(value);
  return Number.isFinite(parsed) &&
    value !== undefined &&
    value !== null &&
    value !== ""
    ? parsed
    : undefined;
}
