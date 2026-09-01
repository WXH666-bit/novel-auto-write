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
  AuthConfig,
  AuthSession,
  User,
} from "./types";

const API_ROOT = (import.meta.env.VITE_API_BASE_URL || "/api").replace(
  /\/$/,
  "",
);

type AuthListener = (status: "unauthorized") => void;
const authListeners = new Set<AuthListener>();

export function onAuthEvent(listener: AuthListener) {
  authListeners.add(listener);
  return () => {
    authListeners.delete(listener);
  };
}

function readCookie(name: string) {
  if (typeof document === "undefined") return "";
  const encoded = `${name}=`;
  const cookie = document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith(encoded));
  return cookie ? decodeURIComponent(cookie.slice(encoded.length)) : "";
}

function isUnsafe(method?: string) {
  return !["GET", "HEAD", "OPTIONS"].includes((method || "GET").toUpperCase());
}

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

function errorMessage(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map(errorMessage).filter(Boolean).join("；");
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.message === "string") return record.message;
    if (typeof record.msg === "string") return record.msg;
    if (record.detail !== undefined) return errorMessage(record.detail);
    try {
      return JSON.stringify(value);
    } catch {
      return "";
    }
  }
  return value === undefined || value === null ? "" : String(value);
}

function parseApiError(raw: string): { message: string; code: string } {
  try {
    const parsed = JSON.parse(raw) as {
      detail?: unknown;
      code?: unknown;
      error?: { code?: unknown; message?: unknown };
    };
    const detail = parsed.detail;
    const detailRecord =
      detail && typeof detail === "object"
        ? (detail as Record<string, unknown>)
        : undefined;
    const code = String(
      detailRecord?.code ?? parsed.code ?? parsed.error?.code ?? "",
    );
    const message = errorMessage(
      detailRecord?.message ??
        detailRecord?.detail ??
        detail ??
        parsed.error?.message ??
        raw,
    );
    return { message, code };
  } catch {
    return { message: raw, code: "" };
  }
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();
  const csrf = readCookie("novel_csrf") || readCookie("csrf_token");
  if (isUnsafe(method) && csrf) headers.set("X-CSRF-Token", csrf);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  });
  if (!response.ok) {
    if (response.status === 401) {
      authListeners.forEach((listener) => listener("unauthorized"));
    }
    let detail = "";
    let code = "";
    try {
      const raw = await response.text();
      ({ message: detail, code } = parseApiError(raw));
    } catch {
      /* server may close early */
    }
    const error = new Error(detail || `请求失败（${response.status}）`);
    Object.assign(error, { status: response.status, code });
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function apiErrorStatus(error: unknown) {
  return (error as Error & { status?: number })?.status;
}

export function apiErrorCode(error: unknown) {
  return (error as Error & { code?: string })?.code;
}

export function normalizeUser(value: unknown): User {
  const source = (value || {}) as Record<string, unknown>;
  return {
    id: String(source.id ?? source.user_id ?? ""),
    email: String(source.email ?? source.email_address ?? "") || undefined,
    username: String(source.username ?? source.user_name ?? "") || undefined,
    display_name: String(source.display_name ?? source.name ?? "") || undefined,
    is_email_verified: Boolean(
      source.is_email_verified ?? source.email_verified ?? source.verified,
    ),
    is_active:
      source.is_active === undefined ? true : Boolean(source.is_active),
    default_provider_id:
      source.default_provider_id === null || source.default_provider_id === undefined
        ? null
        : String(source.default_provider_id),
    created_at: String(source.created_at ?? "") || undefined,
  };
}

export function normalizeAuthSession(value: unknown): AuthSession {
  const source = (value || {}) as Record<string, unknown>;
  const userSource = source.user ?? source.account ?? source;
  const session =
    source.session && typeof source.session === "object"
      ? (source.session as Record<string, unknown>)
      : {};
  return {
    user: normalizeUser(userSource),
    csrf_token: String(
      source.csrf_token ?? source.csrfToken ?? session.csrf_token ?? "",
    ) || undefined,
  };
}

export async function getCurrentUser(): Promise<AuthSession> {
  return normalizeAuthSession(await apiRequest<unknown>("/auth/me"));
}

export function normalizeAuthConfig(value: unknown): AuthConfig {
  const source = (value || {}) as Record<string, unknown>;
  const configSource =
    source.config && typeof source.config === "object"
      ? (source.config as Record<string, unknown>)
      : source.data && typeof source.data === "object"
        ? (source.data as Record<string, unknown>)
        : source;
  const mode = configSource.mode;
  if (mode !== "email" && mode !== "username") {
    throw new Error("服务器登录配置无效");
  }
  if (
    typeof configSource.verification_required !== "boolean" ||
    typeof configSource.password_reset_available !== "boolean"
  ) {
    throw new Error("服务器登录配置不完整");
  }
  return {
    mode,
    verification_required: configSource.verification_required,
    password_reset_available: configSource.password_reset_available,
  };
}

export async function getAuthConfig(): Promise<AuthConfig> {
  return normalizeAuthConfig(await apiRequest<unknown>("/auth/config"));
}

export async function registerAccount(input: {
  email?: string;
  username?: string;
  password: string;
  display_name?: string;
}) {
  const payload: Record<string, unknown> = {
    password: input.password,
    display_name: input.display_name,
  };
  if (input.username?.trim()) payload.username = input.username.trim();
  if (input.email?.trim()) payload.email = input.email.trim();
  if (!payload.username && !payload.email) {
    throw new Error("请输入用户名或邮箱");
  }
  return normalizeAuthSession(
    await apiRequest<unknown>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  );
}

export async function verifyEmail(token: string) {
  return apiRequest<unknown>("/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export async function resendVerification(email?: string) {
  const normalizedEmail = email?.trim() || "";
  if (!normalizedEmail) {
    throw new Error("请输入注册邮箱后重新发送验证邮件。");
  }
  return apiRequest<unknown>("/auth/resend-verification", {
    method: "POST",
    body: JSON.stringify({ email: normalizedEmail }),
  });
}

export async function loginAccount(input: {
  identifier?: string;
  email?: string;
  username?: string;
  password: string;
}) {
  const identifier =
    input.identifier?.trim() || input.username?.trim() || input.email?.trim();
  if (!identifier) throw new Error("请输入用户名或邮箱");
  return normalizeAuthSession(
    await apiRequest<unknown>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ identifier, password: input.password }),
    }),
  );
}

export async function logoutAccount() {
  return apiRequest<unknown>("/auth/logout", { method: "POST" });
}

export async function logoutAllSessions() {
  return apiRequest<unknown>("/auth/logout-all", { method: "POST" });
}

export async function forgotPassword(email: string) {
  return apiRequest<unknown>("/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export async function resetPassword(input: { token: string; password: string }) {
  return apiRequest<unknown>("/auth/reset-password", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function changePassword(input: {
  current_password: string;
  new_password: string;
  revoke_other_sessions?: boolean;
}) {
  return normalizeAuthSession(
    await apiRequest<unknown>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  );
}

export async function deleteAccount(password?: string) {
  return apiRequest<unknown>("/auth/account", {
    method: "DELETE",
    body: JSON.stringify(password ? { password } : {}),
  });
}

function errorStatus(error: unknown) {
  return apiErrorStatus(error);
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
      ...(input.word_target !== undefined || input.target_word_count !== undefined
        ? {
            target_word_count:
              input.target_word_count ?? input.word_target,
          }
        : {}),
    }),
  });
  return normalizeProject(payload);
}

export async function updateProject(
  projectId: string,
  input: Partial<Project>,
  options: { keepalive?: boolean } = {},
): Promise<Project> {
  const payload = await apiRequest<unknown>(`/projects/${projectId}`, {
    method: "PATCH",
    keepalive: options.keepalive,
    body: JSON.stringify({
      ...input,
      ...(input.title !== undefined ? { name: input.title } : {}),
      ...(input.logline !== undefined ? { description: input.logline } : {}),
      ...(input.word_target !== undefined || input.target_word_count !== undefined
        ? {
            target_word_count:
              input.target_word_count ?? input.word_target,
          }
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
  const chapterCount =
    input.mode === "next_chapter"
      ? Math.min(10, Math.max(1, Number(input.chapter_count ?? 1) || 1))
      : 1;
  const instructions = [
    input.instructions,
    input.must ? `必须发生：${String(input.must)}` : "",
    input.must_not ? `禁止发生：${String(input.must_not)}` : "",
  ]
    .filter(Boolean)
    .join("\n");
  const body = {
    chapter_id: input.chapter_id || null,
    provider_id: input.provider_id ? String(input.provider_id) : null,
    idempotency_key: input.idempotency_key || crypto.randomUUID(),
    chapter_count: chapterCount,
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
  const source = new EventSource(generationEventsUrl(jobId), {
    withCredentials: true,
  });
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
  return normalizeProvider(await apiRequest<unknown>("/providers/default"));
}

export async function getProviders(): Promise<ProviderProfile[]> {
  const payload = await apiRequest<unknown>("/providers");
  const result = unwrap<unknown>(payload, ["providers", "items", "data"]);
  return (Array.isArray(result) ? result : []).map(normalizeProvider);
}

export async function createProvider(input: Partial<ProviderProfile>) {
  return normalizeProvider(
    await apiRequest<unknown>("/providers", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  );
}

export async function updateProvider(
  providerId: string,
  input: Partial<ProviderProfile>,
) {
  return normalizeProvider(
    await apiRequest<unknown>(`/providers/${providerId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  );
}

export async function deleteProvider(providerId: string) {
  return apiRequest<unknown>(`/providers/${providerId}`, { method: "DELETE" });
}

export async function setDefaultProvider(providerId: string) {
  return normalizeProvider(
    await apiRequest<unknown>(`/providers/${providerId}/default`, {
      method: "PUT",
    }),
  );
}

export async function deleteProviderKey(providerId: string) {
  return apiRequest<unknown>(`/providers/${providerId}/key`, {
    method: "DELETE",
  });
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

export async function testProvider(
  input: Partial<ProviderProfile>,
): Promise<{
  ok: boolean;
  latency_ms?: number;
  model?: string;
  message?: string;
}> {
  // A saved profile can use the key held by the credential manager.  Use the
  // transient endpoint when the form is new or supplies an unsaved key.
  const savedProviderId =
    typeof input.id === "string" && input.id.trim() && !input.api_key?.trim()
      ? encodeURIComponent(input.id.trim())
      : "";
  const request: RequestInit = { method: "POST" };
  if (!savedProviderId) request.body = JSON.stringify(input);
  const payload = await apiRequest<unknown>(
    savedProviderId ? `/providers/${savedProviderId}/test` : "/providers/test",
    request,
  );
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
  const response = await fetch(`${API_ROOT}/projects/${projectId}/export`, {
    credentials: "include",
  });
  if (!response.ok) {
    if (response.status === 401) {
      authListeners.forEach((listener) => listener("unauthorized"));
    }
    let detail = "";
    let code = "";
    try {
      const raw = await response.text();
      ({ message: detail, code } = parseApiError(raw));
    } catch {
      /* server may close early */
    }
    const error = new Error(detail || `导出失败（${response.status}）`);
    Object.assign(error, { status: response.status, code });
    throw error;
  }
  return response.blob();
}

export function normalizeProject(value: unknown): Project {
  const source = (value || {}) as Record<string, unknown>;
  const targetWordCount = numberOrUndefined(
    source.word_target ??
      source.wordTarget ??
      source.target_word_count ??
      source.targetWordCount,
  );
  return {
    id: String(source.id ?? source.project_id ?? crypto.randomUUID()),
    title: String(source.title ?? source.name ?? "未命名项目"),
    logline: String(
      source.logline ?? source.summary ?? source.description ?? "",
    ),
    genre: String(source.genre ?? source.category ?? ""),
    viewpoint: String(source.viewpoint ?? source.pov ?? ""),
    tone: String(source.tone ?? source.style ?? ""),
    word_target: targetWordCount,
    target_word_count: targetWordCount,
    chapter_target: numberOrUndefined(
      source.chapter_target ?? source.chapterTarget,
    ),
    must_happen: textList(
      source.must_happen ?? source.mustHappen ?? source.must,
    ),
    must_not_happen: textList(
      source.must_not_happen ?? source.mustNotHappen ?? source.must_not,
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
    "running",
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
    running: "运行中",
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
    chapter_count: numberOrUndefined(
      source.chapter_count ??
        source.chapterCount ??
        source.batch_total ??
        source.batchTotal,
    ),
    chapter_index: numberOrUndefined(
      source.chapter_index ??
        source.chapterIndex ??
        source.batch_index ??
        source.batchIndex,
    ),
    batch_index: numberOrUndefined(
      source.batch_index ??
        source.batchIndex ??
        source.chapter_index ??
        source.chapterIndex,
    ),
    batch_total: numberOrUndefined(
      source.batch_total ??
        source.batchTotal ??
        source.chapter_count ??
        source.chapterCount,
    ),
    batch_remaining: numberOrUndefined(
      source.batch_remaining ?? source.batchRemaining,
    ),
    created_at: String(source.created_at ?? source.createdAt ?? ""),
    error: source.error as string | undefined,
    provider_name: String(source.provider_name ?? source.providerName ?? ""),
    provider_id:
      String(source.provider_id ?? source.providerId ?? "") || undefined,
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
    api_version: String(source.api_version ?? source.apiVersion ?? "") || undefined,
    max_output_tokens: numberOrUndefined(
      source.max_output_tokens ?? source.maxOutputTokens,
    ),
    anthropic_workspace_id:
      String(source.anthropic_workspace_id ?? source.anthropicWorkspaceId ?? "") ||
      undefined,
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
    enabled:
      source.enabled === undefined ? true : Boolean(source.enabled),
    is_default: Boolean(source.is_default ?? source.isDefault ?? false),
    deleted_at:
      source.deleted_at === null || source.deleted_at === undefined
        ? null
        : String(source.deleted_at),
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

function textList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === "string") return item.trim();
        if (item === undefined || item === null) return "";
        try {
          return (JSON.stringify(item) ?? "").trim();
        } catch {
          return String(item).trim();
        }
      })
      .filter(Boolean);
  }
  if (typeof value === "string") {
    return value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [];
}
