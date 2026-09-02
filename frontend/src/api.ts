import type {
  AccountPreferences,
  AgentPatch,
  AgentContextSnapshot,
  AgentTarget,
  AssistantConversation,
  AssistantEvent,
  AssistantMessage,
  AssistantProposal,
  AssistantRun,
  CanonItem,
  CharacterCard,
  Chapter,
  GenerationJob,
  ImportPreview,
  MemoryRun,
  MemoryRunEvent,
  PlotThread,
  Project,
  ProjectAttention,
  PortraitAsset,
  ProviderProfile,
  ReviewBundle,
  SourceRef,
  StoryGraph,
  StoryGraphEdge,
  StoryGraphNode,
  StoryMap,
  ProjectMemory,
  StorySummary,
  StartMode,
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
      source.default_provider_id === null ||
      source.default_provider_id === undefined
        ? null
        : String(source.default_provider_id),
    created_at: String(source.created_at ?? "") || undefined,
  };
}

export function normalizeAccountPreferences(
  value: unknown,
): AccountPreferences {
  const source = (value || {}) as Record<string, unknown>;
  const payload =
    source.preferences && typeof source.preferences === "object"
      ? (source.preferences as Record<string, unknown>)
      : source.data && typeof source.data === "object"
        ? (source.data as Record<string, unknown>)
        : source;
  const defaultStartMode =
    payload.default_start_mode ?? payload.defaultStartMode;
  return {
    auto_summary_enabled: Boolean(
      payload.auto_summary_enabled ?? payload.autoSummaryEnabled ?? true,
    ),
    default_start_mode:
      defaultStartMode === "import" || defaultStartMode === "setup"
        ? defaultStartMode
        : "blank",
    preferences_version: numberOrUndefined(
      payload.preferences_version ?? payload.preferencesVersion,
    ),
    updated_at:
      String(payload.updated_at ?? payload.updatedAt ?? "") || undefined,
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
    csrf_token:
      String(
        source.csrf_token ?? source.csrfToken ?? session.csrf_token ?? "",
      ) || undefined,
  };
}

export async function getCurrentUser(): Promise<AuthSession> {
  return normalizeAuthSession(await apiRequest<unknown>("/auth/me"));
}

export async function getAccountPreferences(): Promise<AccountPreferences> {
  return normalizeAccountPreferences(
    await apiRequest<unknown>("/account/preferences"),
  );
}

export async function updateAccountPreferences(input: {
  auto_summary_enabled?: boolean;
  expected_version?: number;
}): Promise<AccountPreferences> {
  return normalizeAccountPreferences(
    await apiRequest<unknown>("/account/preferences", {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  );
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

export async function resetPassword(input: {
  token: string;
  password: string;
}) {
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

export async function createProject(
  input: Partial<Project> & {
    start_mode?: StartMode;
    first_chapter_title?: string;
  },
): Promise<Project> {
  const payload = await apiRequest<unknown>("/projects", {
    method: "POST",
    body: JSON.stringify({
      ...input,
      name: input.title,
      description: input.logline,
      ...(input.word_target !== undefined ||
      input.target_word_count !== undefined
        ? {
            target_word_count: input.target_word_count ?? input.word_target,
          }
        : {}),
      ...(input.start_mode ? { start_mode: input.start_mode } : {}),
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
      ...(input.word_target !== undefined ||
      input.target_word_count !== undefined
        ? {
            target_word_count: input.target_word_count ?? input.word_target,
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

export async function createChapter(
  projectId: string,
  input: {
    title?: string;
    content?: string;
    summary?: string;
    status?: Chapter["status"] | string;
    volume_number?: number;
    chapter_number?: number;
    sort_order?: number;
  } = {},
): Promise<Chapter> {
  return normalizeChapter(
    await apiRequest<unknown>(`/projects/${projectId}/chapters`, {
      method: "POST",
      body: JSON.stringify({
        volume_number: input.volume_number ?? 1,
        ...(input.chapter_number
          ? { chapter_number: input.chapter_number }
          : {}),
        ...(input.sort_order !== undefined
          ? { sort_order: input.sort_order }
          : {}),
        title: input.title || "未命名章节",
        status: input.status || "draft",
        summary: input.summary ?? null,
        content: input.content ?? "",
      }),
    }),
  );
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

export interface ChapterCompletion {
  chapter: Chapter;
  memory_run?: MemoryRun | null;
  auto_summary_enabled?: boolean;
}

export async function completeChapter(
  chapterId: string,
  input: { expected_revision_id?: string; analyze?: boolean } = {},
): Promise<ChapterCompletion> {
  const payload = await apiRequest<unknown>(`/chapters/${chapterId}/complete`, {
    method: "POST",
    body: JSON.stringify(input),
  });
  const source = (payload || {}) as Record<string, unknown>;
  return {
    chapter: normalizeChapter(source.chapter ?? payload),
    memory_run: source.memory_run
      ? normalizeMemoryRun(source.memory_run)
      : null,
    auto_summary_enabled:
      source.auto_summary_enabled === undefined
        ? undefined
        : Boolean(source.auto_summary_enabled),
  };
}

export async function getCanon(projectId: string): Promise<CanonItem[]> {
  const payload = await apiRequest<unknown>(`/projects/${projectId}/canon`);
  const result = unwrap<unknown>(payload, ["canon", "canon_items", "items"]);
  return (Array.isArray(result) ? result : []).map(normalizeCanon);
}

export async function createCanon(
  projectId: string,
  input: Record<string, unknown>,
): Promise<CanonItem> {
  return normalizeCanon(
    await apiRequest<unknown>(`/projects/${projectId}/canon`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  );
}

export async function updateCanon(
  canonId: string,
  input: Record<string, unknown>,
): Promise<CanonItem> {
  return normalizeCanon(
    await apiRequest<unknown>(`/canon/${canonId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  );
}

export async function confirmCanon(
  canonId: string,
  input: { reason?: string; force?: boolean } = {},
): Promise<CanonItem> {
  return normalizeCanon(
    await apiRequest<unknown>(`/canon/${canonId}/confirm`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  );
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
    graph: raw.graph ? normalizeStoryGraph(raw.graph) : undefined,
  };
}

export async function analyzeProjectMemory(
  projectId: string,
  input: { scope?: "project" | "chapter"; chapter_id?: string } = {},
): Promise<MemoryRun> {
  return normalizeMemoryRun(
    await apiRequest<unknown>(`/projects/${projectId}/memory/analyze`, {
      method: "POST",
      body: JSON.stringify({
        scope: input.scope || "project",
        ...(input.chapter_id ? { chapter_id: input.chapter_id } : {}),
      }),
    }),
  );
}

export async function getProjectMemory(
  projectId: string,
): Promise<ProjectMemory> {
  const payload = await apiRequest<unknown>(`/projects/${projectId}/memory`);
  const raw = (unwrap<Record<string, unknown>>(payload, ["memory", "data"]) ||
    {}) as Record<string, unknown>;
  const projectSummary = raw.project_summary ?? raw.projectSummary;
  const chapterRows = raw.chapter_summaries ?? raw.chapterSummaries;
  const runRows = raw.runs ?? raw.memory_runs ?? raw.memoryRuns;
  return {
    project_id: String(raw.project_id ?? raw.projectId ?? projectId),
    memory_epoch: Number(raw.memory_epoch ?? raw.memoryEpoch ?? 0) || 0,
    auto_summary_enabled:
      raw.auto_summary_enabled === undefined
        ? true
        : Boolean(raw.auto_summary_enabled),
    project_summary: projectSummary
      ? normalizeStorySummary(projectSummary, projectId)
      : null,
    chapter_summaries: Array.isArray(chapterRows)
      ? chapterRows.map((item) => normalizeStorySummary(item, projectId))
      : [],
    runs: Array.isArray(runRows) ? runRows.map(normalizeMemoryRun) : [],
  };
}

export async function getMemoryRun(runId: string): Promise<MemoryRun> {
  return normalizeMemoryRun(
    await apiRequest<unknown>(`/memory-runs/${encodeURIComponent(runId)}`),
  );
}

export async function retryMemoryRun(runId: string): Promise<MemoryRun> {
  return normalizeMemoryRun(
    await apiRequest<unknown>(
      `/memory-runs/${encodeURIComponent(runId)}/retry`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
    ),
  );
}

export function memoryRunEventsUrl(runId: string) {
  return `${API_ROOT}/memory-runs/${encodeURIComponent(runId)}/events`;
}

export function listenMemoryRunEvents(
  runId: string,
  onEvent: (event: MemoryRunEvent) => void,
  onError?: () => void,
) {
  const source = new EventSource(memoryRunEventsUrl(runId), {
    withCredentials: true,
  });
  const parseProgress = (event: MessageEvent<string>) => {
    try {
      const payload = JSON.parse(event.data) as unknown;
      onEvent({ type: "progress", run: normalizeMemoryRun(payload) });
    } catch {
      /* malformed memory events are ignored; polling remains authoritative */
    }
  };
  const parseArtifact = (event: MessageEvent<string>) => {
    try {
      const payload = JSON.parse(event.data) as Record<string, unknown>;
      onEvent({
        type: "artifact",
        sequence: Number(payload.sequence ?? 0) || 0,
        stage: String(payload.stage ?? "") || undefined,
        content_hash:
          String(payload.content_hash ?? payload.contentHash ?? "") ||
          undefined,
      });
    } catch {
      /* malformed memory events are ignored */
    }
  };
  source.addEventListener("progress", parseProgress as EventListener);
  source.addEventListener("artifact", parseArtifact as EventListener);
  source.onmessage = parseProgress;
  source.onerror = () => onError?.();
  return () => source.close();
}

export async function getCharacters(
  projectId: string,
): Promise<CharacterCard[]> {
  const payload = await apiRequest<unknown>(
    `/projects/${projectId}/characters`,
  );
  const result = unwrap<unknown>(payload, ["characters", "items", "data"]);
  return (Array.isArray(result) ? result : []).map((item) =>
    normalizeCharacter(item, projectId),
  );
}

export async function createCharacter(
  projectId: string,
  input: Partial<CharacterCard>,
): Promise<CharacterCard> {
  return normalizeCharacter(
    await apiRequest<unknown>(`/projects/${projectId}/characters`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
    projectId,
  );
}

export async function updateCharacter(
  projectId: string,
  characterId: string,
  input: Partial<CharacterCard>,
): Promise<CharacterCard> {
  return normalizeCharacter(
    await apiRequest<unknown>(
      `/projects/${projectId}/characters/${characterId}`,
      {
        method: "PATCH",
        body: JSON.stringify(input),
      },
    ),
    projectId,
  );
}

export async function getCharacter(
  characterId: string,
): Promise<CharacterCard> {
  return normalizeCharacter(
    await apiRequest<unknown>(`/characters/${encodeURIComponent(characterId)}`),
  );
}

export async function deleteCharacter(
  projectId: string,
  characterId: string,
): Promise<void> {
  await apiRequest<unknown>(
    `/projects/${projectId}/characters/${characterId}`,
    { method: "DELETE" },
  );
}

export async function uploadCharacterPortrait(
  projectId: string,
  characterId: string,
  file: File,
  alt = "",
): Promise<PortraitAsset> {
  const form = new FormData();
  form.append("file", file);
  if (alt.trim()) form.append("alt", alt.trim());
  const response = await apiRequest<unknown>(
    `/characters/${encodeURIComponent(characterId)}/portrait`,
    {
      method: "POST",
      body: form,
    },
  );
  return normalizePortrait(response, projectId);
}

export async function deleteCharacterPortrait(
  projectId: string,
  characterId: string,
  mediaId?: string | null,
): Promise<void> {
  try {
    await apiRequest<unknown>(
      `/characters/${encodeURIComponent(characterId)}/portrait`,
      {
        method: "DELETE",
      },
    );
  } catch (error) {
    if (apiErrorStatus(error) !== 404) throw error;
  }
  void projectId;
  void mediaId;
}

export async function getStoryGraph(projectId: string): Promise<StoryGraph> {
  return normalizeStoryGraph(
    await apiRequest<unknown>(`/projects/${projectId}/story-graph`),
  );
}

export async function saveStoryGraph(
  projectId: string,
  graph: StoryGraph,
  options: {
    deletedEdgeIds?: string[];
    expectedLayoutVersion?: number;
  } = {},
): Promise<StoryGraph> {
  const nodeIds = new Map<string, string>();
  for (const node of graph.nodes) {
    // Existing graph nodes are semantic records.  A canvas drag must never
    // PATCH one of those records (that would bump memory_epoch); only the
    // separate layout endpoint receives positions.  Fallback nodes are the
    // only nodes that need to be materialised here.
    const synthetic =
      /^(character|thread|event)-/.test(node.id) ||
      node.data?.is_fallback === true;
    if (!synthetic) {
      nodeIds.set(node.id, node.id);
      continue;
    }
    const nodeData = {
      ...(node.data || {}),
      ...(node.ref_id ? { ref_id: node.ref_id } : {}),
      ...(node.character_id ? { character_id: node.character_id } : {}),
      ...(node.chapter_id ? { chapter_id: node.chapter_id } : {}),
      ...(node.plot_thread_id ? { plot_thread_id: node.plot_thread_id } : {}),
      ...(node.source_refs?.length ? { source_refs: node.source_refs } : {}),
    };
    const payload = {
      node_type: node.type === "thread" ? "plot" : node.type,
      ref_id:
        node.ref_id ??
        (node.data?.ref_id as string | undefined) ??
        (node.data?.character_id as string | undefined) ??
        (node.data?.thread_id as string | undefined) ??
        null,
      character_id:
        node.character_id ??
        (node.data?.character_id as string | undefined) ??
        null,
      chapter_id:
        node.chapter_id ??
        (node.data?.chapter_id as string | undefined) ??
        null,
      plot_thread_id:
        node.plot_thread_id ??
        (node.data?.plot_thread_id as string | undefined) ??
        (node.data?.thread_id as string | undefined) ??
        null,
      label: node.label,
      data: nodeData,
      position_x: node.position.x,
      position_y: node.position.y,
      status: node.status || "active",
    };
    const saved = await apiRequest<unknown>(
      `/projects/${projectId}/story-graph/nodes`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
    const normalized = normalizeStoryGraphNode(saved);
    if (normalized) nodeIds.set(node.id, normalized.id);
  }
  for (const edge of graph.edges) {
    const source = nodeIds.get(edge.source) ?? edge.source;
    const target = nodeIds.get(edge.target) ?? edge.target;
    const payload = {
      source_node_id: source,
      target_node_id: target,
      relation_type: edge.relation_type ?? edge.kind ?? "related",
      label: edge.label ?? null,
      directed: edge.directed ?? edge.direction === "directed",
      weight: edge.weight ?? null,
      data: {
        ...(edge.data || {}),
        ...(edge.source_refs?.length ? { source_refs: edge.source_refs } : {}),
      },
      status: edge.status || "active",
    };
    if (!edge.id.startsWith("edge-") && edge.version !== undefined) {
      await apiRequest<unknown>(
        `/projects/${projectId}/story-graph/edges/${encodeURIComponent(edge.id)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ ...payload, expected_version: edge.version }),
        },
      );
    } else {
      await apiRequest<unknown>(`/projects/${projectId}/story-graph/edges`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
    }
  }
  for (const edgeId of options.deletedEdgeIds || []) {
    try {
      await apiRequest<unknown>(
        `/projects/${projectId}/story-graph/edges/${encodeURIComponent(edgeId)}`,
        { method: "DELETE" },
      );
    } catch (error) {
      if (apiErrorStatus(error) !== 404) throw error;
    }
  }
  await saveStoryGraphLayout(
    projectId,
    {
      nodes: graph.nodes.map((node) => ({
        id: nodeIds.get(node.id) ?? node.id,
        x: node.position.x,
        y: node.position.y,
      })),
    },
    options.expectedLayoutVersion ?? graph.layout_version ?? graph.version,
  );
  return getStoryGraph(projectId);
}

export async function saveStoryGraphLayout(
  projectId: string,
  layout: Record<string, unknown>,
  expectedVersion?: number,
): Promise<unknown> {
  return apiRequest<unknown>(`/projects/${projectId}/story-graph/layout`, {
    method: "PATCH",
    body: JSON.stringify({
      layout_json: layout,
      ...(expectedVersion !== undefined
        ? { expected_version: expectedVersion }
        : {}),
    }),
  });
}

export async function createAssistantConversation(
  projectId: string,
  input: { target: AgentTarget; title?: string },
): Promise<AssistantConversation> {
  return normalizeAssistantConversation(
    await apiRequest<unknown>(
      `/projects/${projectId}/assistant/conversations`,
      {
        method: "POST",
        body: JSON.stringify({
          title: input.title || "故事设定助手",
          purpose:
            input.target.type === "character" ? "setup_character" : "setup",
          apply_mode: "preview",
          target: input.target,
        }),
      },
    ),
    projectId,
  );
}

export async function getAssistantConversation(
  projectId: string,
  conversationId: string,
): Promise<AssistantConversation> {
  return normalizeAssistantConversation(
    await apiRequest<unknown>(
      `/projects/${projectId}/assistant/conversations/${conversationId}`,
    ),
    projectId,
  );
}

export async function listAssistantConversations(
  projectId: string,
): Promise<AssistantConversation[]> {
  const payload = await apiRequest<unknown>(
    `/projects/${projectId}/assistant/conversations`,
  );
  const rows = unwrap<unknown>(payload, ["conversations", "items", "data"]);
  return (Array.isArray(rows) ? rows : []).map((item) =>
    normalizeAssistantConversation(item, projectId),
  );
}

export async function listAssistantMessages(
  projectId: string,
  conversationId: string,
): Promise<AssistantMessage[]> {
  const payload = await apiRequest<unknown>(
    `/projects/${projectId}/assistant/conversations/${conversationId}/messages`,
  );
  const rows = unwrap<unknown>(payload, ["messages", "items", "data"]);
  return (Array.isArray(rows) ? rows : []).map(normalizeAssistantMessage);
}

export async function listAssistantProposals(
  projectId: string,
  conversationId?: string,
): Promise<AssistantProposal[]> {
  const query = conversationId
    ? `?conversation_id=${encodeURIComponent(conversationId)}`
    : "";
  const payload = await apiRequest<unknown>(
    `/projects/${projectId}/assistant/proposals${query}`,
  );
  const rows = unwrap<unknown>(payload, ["proposals", "items", "data"]);
  return (Array.isArray(rows) ? rows : []).map(normalizeAssistantProposal);
}

export async function sendAssistantMessage(
  projectId: string,
  conversationId: string,
  content: string,
  options: {
    target?: AgentTarget;
    context_snapshot?: AgentContextSnapshot;
    authorized_asset_ids?: string[];
    expected_version?: number;
    idempotency_key?: string;
  } = {},
): Promise<AssistantMessage> {
  const payload = await apiRequest<unknown>(
    `/projects/${projectId}/assistant/conversations/${conversationId}/messages`,
    {
      method: "POST",
      body: JSON.stringify({
        content,
        idempotency_key: options.idempotency_key || crypto.randomUUID(),
        target: options.target || {},
        context_snapshot: options.context_snapshot || {},
        authorized_asset_ids: options.authorized_asset_ids || [],
        ...(options.expected_version
          ? { expected_version: options.expected_version }
          : {}),
      }),
    },
  );
  const message = unwrap<unknown>(payload, ["message", "data"]);
  return normalizeAssistantMessage(message);
}

export async function applyAssistantProposal(
  projectId: string,
  conversationId: string,
  proposalId: string,
  options: {
    expected_version?: number | null;
    expected_memory_epoch?: number | null;
    reason?: string;
  } = {},
): Promise<AssistantProposal> {
  void conversationId;
  return normalizeAssistantProposal(
    await apiRequest<unknown>(
      `/projects/${projectId}/assistant/proposals/${proposalId}/apply`,
      {
        method: "POST",
        body: JSON.stringify({
          ...(options.expected_version != null
            ? { expected_version: options.expected_version }
            : {}),
          ...(options.expected_memory_epoch != null
            ? { expected_memory_epoch: options.expected_memory_epoch }
            : {}),
          ...(options.reason ? { reason: options.reason } : {}),
        }),
      },
    ),
  );
}

export async function rejectAssistantProposal(
  projectId: string,
  conversationId: string,
  proposalId: string,
  options: { reason?: string } = {},
): Promise<AssistantProposal> {
  void conversationId;
  return normalizeAssistantProposal(
    await apiRequest<unknown>(
      `/projects/${projectId}/assistant/proposals/${proposalId}/reject`,
      {
        method: "POST",
        body: JSON.stringify(options.reason ? { reason: options.reason } : {}),
      },
    ),
  );
}

export async function applyAssistantProposals(
  projectId: string,
  proposalIds: string[],
  options: {
    expected_memory_epoch?: number | null;
    expected_versions?: Record<string, number>;
    reason?: string;
  } = {},
): Promise<AssistantProposal[]> {
  const payload = await apiRequest<Record<string, unknown>>(
    `/projects/${projectId}/assistant/proposals/apply-batch`,
    {
      method: "POST",
      body: JSON.stringify({
        proposal_ids: proposalIds,
        ...(options.expected_memory_epoch != null
          ? { expected_memory_epoch: options.expected_memory_epoch }
          : {}),
        ...(options.expected_versions
          ? { expected_versions: options.expected_versions }
          : {}),
        ...(options.reason ? { reason: options.reason } : {}),
      }),
    },
  );
  const rows = unwrap<unknown>(payload, ["proposals", "items", "data"]);
  return (Array.isArray(rows) ? rows : []).map(normalizeAssistantProposal);
}

export async function rejectAssistantProposals(
  projectId: string,
  proposalIds: string[],
  options: { reason?: string } = {},
): Promise<AssistantProposal[]> {
  const payload = await apiRequest<Record<string, unknown>>(
    `/projects/${projectId}/assistant/proposals/reject-batch`,
    {
      method: "POST",
      body: JSON.stringify({
        proposal_ids: proposalIds,
        ...(options.reason ? { reason: options.reason } : {}),
      }),
    },
  );
  const rows = unwrap<unknown>(payload, ["proposals", "items", "data"]);
  return (Array.isArray(rows) ? rows : []).map(normalizeAssistantProposal);
}

export async function listAssistantRuns(
  projectId: string,
  conversationId: string,
): Promise<AssistantRun[]> {
  const payload = await apiRequest<unknown>(
    `/projects/${projectId}/assistant/conversations/${conversationId}/runs`,
  );
  const rows = unwrap<unknown>(payload, ["runs", "items", "data"]);
  return (Array.isArray(rows) ? rows : []).map(normalizeAssistantRun);
}

export async function retryAssistantRun(
  projectId: string,
  conversationId: string,
  runId: string,
): Promise<AssistantRun> {
  return normalizeAssistantRun(
    await apiRequest<unknown>(
      `/projects/${projectId}/assistant/conversations/${conversationId}/runs/${runId}/retry`,
      { method: "POST" },
    ),
  );
}

export function assistantEventsUrl(
  projectId: string,
  conversationId: string,
  after?: number,
) {
  const query = after && after > 0 ? `?after=${encodeURIComponent(after)}` : "";
  return `${API_ROOT}/projects/${projectId}/assistant/conversations/${conversationId}/events/stream${query}`;
}

export function listenAssistantEvents(
  projectId: string,
  conversationId: string,
  onEvent: (event: AssistantEvent) => void,
  onReconnect?: () => void,
  after?: number,
  onOpen?: () => void,
) {
  const source = new EventSource(
    assistantEventsUrl(projectId, conversationId, after),
    {
      withCredentials: true,
    },
  );
  const handleEvent = (event: MessageEvent<string>) => {
    try {
      const parsed = JSON.parse(event.data) as unknown;
      onEvent(normalizeAssistantEvent(parsed));
    } catch {
      /* malformed assistant events are ignored */
    }
  };
  source.onmessage = handleEvent;
  source.addEventListener("assistant", handleEvent as EventListener);
  source.onopen = () => onOpen?.();
  source.onerror = () => {
    // Keep the native EventSource alive: the browser reconnects with the
    // persisted Last-Event-ID, so durable deltas resume without duplication.
    onReconnect?.();
  };
  return () => source.close();
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
    destination:
      input.destination === "current_blank" ? "current_blank" : "new_child",
    skip_memory_once: Boolean(input.skip_memory_once),
    ...(input.skip_memory_reason
      ? { skip_memory_reason: String(input.skip_memory_reason) }
      : {}),
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
    // Keep native EventSource reconnect enabled.  The server replays durable
    // progress after Last-Event-ID, so a transient network loss is recoverable.
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

export async function getProjectAttention(
  projectId: string,
): Promise<ProjectAttention> {
  const raw = await apiRequest<Record<string, unknown>>(
    `/projects/${projectId}/attention`,
  );
  const items = Array.isArray(raw.items) ? raw.items : [];
  return {
    total: Number(raw.total || 0),
    reviews: Number(raw.reviews || 0),
    rechecks: Number(raw.rechecks || 0),
    proposals: Number(raw.proposals || 0),
    retries: Number(raw.retries || 0),
    items: items.map((item) => {
      const row = (item || {}) as Record<string, unknown>;
      const rawKind = String(row.kind || "retry");
      const kind =
        rawKind === "review" || rawKind === "recheck" || rawKind === "proposal"
          ? rawKind
          : "retry";
      return {
        id: String(row.id || ""),
        kind,
        status: String(row.status || ""),
        title: String(row.title || "待处理事项"),
        detail: String(row.detail || "") || undefined,
        chapter_id:
          row.chapter_id === null
            ? null
            : String(row.chapter_id || "") || undefined,
        conversation_id:
          row.conversation_id === null
            ? null
            : String(row.conversation_id || "") || undefined,
        run_id:
          row.run_id === null ? null : String(row.run_id || "") || undefined,
        task_type: String(row.task_type || "") || undefined,
        job_id:
          row.job_id === null ? null : String(row.job_id || "") || undefined,
        target_type:
          row.target_type === null
            ? null
            : String(row.target_type || "") || undefined,
        created_at: String(row.created_at || "") || undefined,
      };
    }),
  };
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

export async function testProvider(input: Partial<ProviderProfile>): Promise<{
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
    style: String(source.style ?? source.tone ?? ""),
    story_bible:
      String(source.story_bible ?? source.storyBible ?? "") || undefined,
    outline:
      source.outline && typeof source.outline === "object"
        ? (source.outline as Record<string, unknown>)
        : {},
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
    summary_status: String(
      source.summary_status ?? source.summaryStatus ?? "current",
    ) as Project["summary_status"],
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
    summary_status: String(
      source.summary_status ?? source.summaryStatus ?? "current",
    ) as Chapter["summary_status"],
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

export function normalizeMemoryRun(value: unknown): MemoryRun {
  const source = (unwrap<Record<string, unknown>>(value, [
    "run",
    "memory_run",
  ]) || {}) as Record<string, unknown>;
  const rawStatus = String(source.status ?? source.state ?? "queued");
  const status = (
    [
      "queued",
      "running",
      "current",
      "failed",
      "stale",
      "skipped",
      "cancelled",
      "completed",
      "needs_retry",
    ] as const
  ).includes(rawStatus as MemoryRun["status"])
    ? (rawStatus as MemoryRun["status"])
    : "queued";
  return {
    id: String(source.id ?? source.run_id ?? crypto.randomUUID()),
    project_id: String(source.project_id ?? source.projectId ?? ""),
    status,
    scope:
      source.scope === "project"
        ? "project"
        : source.scope === "chapter"
          ? "chapter"
          : source.scope === "arc"
            ? "arc"
            : "chapters",
    chapter_id:
      source.chapter_id === null
        ? null
        : String(source.chapter_id ?? source.chapterId ?? "") || undefined,
    chapter_ids: Array.isArray(source.chapter_ids ?? source.chapterIds)
      ? ((source.chapter_ids ?? source.chapterIds) as unknown[]).map(String)
      : source.chapter_id
        ? [String(source.chapter_id)]
        : [],
    progress: numberOrUndefined(source.progress ?? source.percent),
    phase_label: String(
      source.phase_label ?? source.phaseLabel ?? source.message ?? "",
    ),
    error:
      String(source.error ?? source.last_error ?? source.lastError ?? "") ||
      undefined,
    created_at:
      String(source.created_at ?? source.createdAt ?? "") || undefined,
    completed_at:
      String(source.completed_at ?? source.completedAt ?? "") || undefined,
    stage: String(source.stage ?? source.current_stage ?? "") || undefined,
    started_at:
      String(source.started_at ?? source.startedAt ?? "") || undefined,
    finished_at:
      String(source.finished_at ?? source.finishedAt ?? "") || undefined,
    idempotency_key:
      String(source.idempotency_key ?? source.idempotencyKey ?? "") ||
      undefined,
    provider_profile_id:
      source.provider_profile_id === null
        ? null
        : String(
            source.provider_profile_id ?? source.providerProfileId ?? "",
          ) || undefined,
  };
}

function normalizeStorySummary(value: unknown, projectId = ""): StorySummary {
  const source = (unwrap<Record<string, unknown>>(value, [
    "summary",
    "story_summary",
    "storySummary",
  ]) || {}) as Record<string, unknown>;
  const structured = source.structured_json ?? source.structuredJson;
  return {
    id: String(source.id ?? source.summary_id ?? crypto.randomUUID()),
    project_id: String(source.project_id ?? source.projectId ?? projectId),
    scope: String(source.scope ?? "project"),
    chapter_id:
      source.chapter_id === null
        ? null
        : String(source.chapter_id ?? source.chapterId ?? "") || undefined,
    current_revision_id:
      source.current_revision_id === null
        ? null
        : String(
            source.current_revision_id ?? source.currentRevisionId ?? "",
          ) || undefined,
    status: String(source.status ?? "current"),
    summary_text: String(
      source.summary_text ?? source.summaryText ?? source.summary ?? "",
    ),
    structured_json:
      structured && typeof structured === "object"
        ? (structured as Record<string, unknown>)
        : {},
    memory_epoch: Number(source.memory_epoch ?? source.memoryEpoch ?? 0) || 0,
    created_at:
      String(source.created_at ?? source.createdAt ?? "") || undefined,
    updated_at:
      String(source.updated_at ?? source.updatedAt ?? "") || undefined,
  };
}

function normalizePortrait(value: unknown, projectId = ""): PortraitAsset {
  const raw = (value || {}) as Record<string, unknown>;
  const source =
    raw.asset && typeof raw.asset === "object"
      ? (raw.asset as Record<string, unknown>)
      : raw.portrait && typeof raw.portrait === "object"
        ? (raw.portrait as Record<string, unknown>)
        : raw;
  return {
    id: String(source.id ?? source.asset_id ?? crypto.randomUUID()),
    project_id: String(source.project_id ?? source.projectId ?? projectId),
    filename:
      String(
        source.filename ?? source.file_name ?? source.original_name ?? "",
      ) || undefined,
    url: String(
      source.url ??
        source.public_url ??
        source.download_url ??
        source.path ??
        "",
    ),
    alt: String(source.alt ?? source.alt_text ?? "") || undefined,
    width: numberOrUndefined(source.width),
    height: numberOrUndefined(source.height),
    content_type:
      String(source.content_type ?? source.contentType ?? source.mime ?? "") ||
      undefined,
    byte_size: numberOrUndefined(
      source.byte_size ?? source.byteSize ?? source.size,
    ),
    checksum: String(source.checksum ?? source.hash ?? "") || undefined,
    created_at:
      String(source.created_at ?? source.createdAt ?? "") || undefined,
  };
}

export function normalizeCharacter(
  value: unknown,
  projectId = "",
): CharacterCard {
  const raw = (value || {}) as Record<string, unknown>;
  const profile =
    raw.profile && typeof raw.profile === "object"
      ? (raw.profile as Record<string, unknown>)
      : {};
  const imageMediaId =
    String(raw.image_media_id ?? raw.imageMediaId ?? "") || undefined;
  const portraitValue = raw.portrait ?? raw.avatar ?? raw.image;
  const rawStatus = String(raw.status ?? "draft");
  const status = (
    [
      "draft",
      "pending",
      "confirmed",
      "needs_review",
      "archived",
      "active",
    ] as const
  ).includes(rawStatus as CharacterCard["status"])
    ? (rawStatus as CharacterCard["status"])
    : "draft";
  const rawData =
    raw.data && typeof raw.data === "object"
      ? (raw.data as Record<string, unknown>)
      : {};
  const refs = Array.isArray(raw.source_refs ?? raw.sourceRefs)
    ? ((raw.source_refs ?? raw.sourceRefs) as unknown[]).map(normalizeSourceRef)
    : Array.isArray(rawData.source_refs ?? rawData.sourceRefs)
      ? ((rawData.source_refs ?? rawData.sourceRefs) as unknown[]).map(
          normalizeSourceRef,
        )
      : [];
  const portrait = portraitValue
    ? normalizePortrait(portraitValue, projectId)
    : imageMediaId
      ? normalizePortrait(
          {
            id: imageMediaId,
            project_id: projectId,
            url: `${API_ROOT}/media/${encodeURIComponent(imageMediaId)}`,
          },
          projectId,
        )
      : null;
  return {
    id: String(raw.id ?? raw.character_id ?? crypto.randomUUID()),
    project_id: String(raw.project_id ?? raw.projectId ?? projectId),
    name: String(
      raw.name ?? raw.key ?? raw.subject ?? profile.name ?? "未命名人物",
    ),
    aliases: textList(raw.aliases ?? profile.aliases),
    role: String(raw.role ?? profile.role ?? "") || undefined,
    age: String(raw.age ?? profile.age ?? "") || undefined,
    gender: String(raw.gender ?? profile.gender ?? "") || undefined,
    pronouns: String(raw.pronouns ?? profile.pronouns ?? "") || undefined,
    appearance: String(raw.appearance ?? profile.appearance ?? "") || undefined,
    personality:
      String(raw.personality ?? profile.personality ?? "") || undefined,
    motivation: String(raw.motivation ?? profile.motivation ?? "") || undefined,
    occupation: String(raw.occupation ?? profile.occupation ?? "") || undefined,
    background: String(raw.background ?? profile.background ?? "") || undefined,
    goals:
      String(raw.goals ?? raw.goal ?? profile.goals ?? profile.goal ?? "") ||
      undefined,
    conflict_fears:
      String(
        raw.conflict_fears ??
          raw.conflict ??
          profile.conflict_fears ??
          profile.conflict ??
          "",
      ) || undefined,
    abilities: String(raw.abilities ?? profile.abilities ?? "") || undefined,
    arc: String(raw.arc ?? raw.character_arc ?? profile.arc ?? "") || undefined,
    voice: String(raw.voice ?? profile.voice ?? "") || undefined,
    tags: textList(raw.tags ?? profile.tags),
    custom_fields:
      raw.custom_fields && typeof raw.custom_fields === "object"
        ? Object.fromEntries(
            Object.entries(raw.custom_fields as Record<string, unknown>).map(
              ([key, item]) => [
                key,
                typeof item === "string" ? item : JSON.stringify(item),
              ],
            ),
          )
        : {},
    portrait,
    status,
    image_media_id: imageMediaId ?? null,
    source_refs: refs,
    canon_item_id:
      String(raw.canon_item_id ?? raw.canonItemId ?? "") || undefined,
    current_revision_id:
      String(raw.current_revision_id ?? raw.currentRevisionId ?? "") ||
      undefined,
    version: numberOrUndefined(raw.version),
    updated_at: String(raw.updated_at ?? raw.updatedAt ?? "") || undefined,
    created_at: String(raw.created_at ?? raw.createdAt ?? "") || undefined,
  };
}

function normalizeStoryGraphNode(value: unknown): StoryGraphNode | null {
  const raw = (value || {}) as Record<string, unknown>;
  const data =
    raw.data && typeof raw.data === "object"
      ? (raw.data as Record<string, unknown>)
      : {};
  const rawType = String(
    raw.type ?? raw.node_type ?? data.type ?? "custom",
  ).toLowerCase();
  const type: StoryGraphNode["type"] | null =
    rawType === "character" || rawType === "person"
      ? "character"
      : rawType === "thread" ||
          rawType === "plot" ||
          rawType === "plot_thread" ||
          rawType === "story_line"
        ? "thread"
        : rawType === "event" || rawType === "timeline"
          ? "event"
          : null;
  // Graph v1 deliberately excludes chapter/setting/custom nodes.  Dropping
  // them here keeps both React Flow and the relation table honest about the
  // supported vocabulary instead of silently relabelling them as events.
  if (!type) return null;
  const position =
    raw.position && typeof raw.position === "object"
      ? (raw.position as Record<string, unknown>)
      : {};
  return {
    id: String(raw.id ?? crypto.randomUUID()),
    type,
    label: String(raw.label ?? raw.name ?? data.label ?? "未命名节点"),
    subtitle: String(raw.subtitle ?? data.subtitle ?? "") || undefined,
    image_url:
      String(raw.image_url ?? raw.imageUrl ?? data.image_url ?? "") ||
      undefined,
    status: String(raw.status ?? data.status ?? "") || undefined,
    position: {
      x: Number(position.x ?? raw.position_x ?? raw.x ?? 0),
      y: Number(position.y ?? raw.position_y ?? raw.y ?? 0),
    },
    data,
    ref_id:
      String(raw.ref_id ?? raw.refId ?? data.ref_id ?? data.refId ?? "") ||
      undefined,
    character_id:
      String(
        raw.character_id ??
          raw.characterId ??
          data.character_id ??
          data.characterId ??
          "",
      ) || undefined,
    chapter_id:
      String(
        raw.chapter_id ??
          raw.chapterId ??
          data.chapter_id ??
          data.chapterId ??
          "",
      ) || undefined,
    plot_thread_id:
      String(
        raw.plot_thread_id ??
          raw.plotThreadId ??
          data.plot_thread_id ??
          data.plotThreadId ??
          data.thread_id ??
          data.threadId ??
          "",
      ) || undefined,
    source_refs: Array.isArray(raw.source_refs ?? raw.sourceRefs)
      ? ((raw.source_refs ?? raw.sourceRefs) as unknown[]).map(
          normalizeSourceRef,
        )
      : Array.isArray(data.source_refs ?? data.sourceRefs)
        ? ((data.source_refs ?? data.sourceRefs) as unknown[]).map(
            normalizeSourceRef,
          )
        : [],
    version: numberOrUndefined(raw.version),
  };
}

function normalizeStoryGraphEdge(value: unknown): StoryGraphEdge {
  const raw = (value || {}) as Record<string, unknown>;
  const rawData =
    raw.data && typeof raw.data === "object"
      ? (raw.data as Record<string, unknown>)
      : {};
  const refs = Array.isArray(raw.source_refs ?? raw.sourceRefs)
    ? ((raw.source_refs ?? raw.sourceRefs) as unknown[]).map(normalizeSourceRef)
    : Array.isArray(rawData.source_refs ?? rawData.sourceRefs)
      ? ((rawData.source_refs ?? rawData.sourceRefs) as unknown[]).map(
          normalizeSourceRef,
        )
      : [];
  const rawStatus = String(raw.status ?? "pending");
  const status = (
    ["active", "draft", "pending", "confirmed", "needs_review"] as const
  ).includes(
    rawStatus as "active" | "draft" | "pending" | "confirmed" | "needs_review",
  )
    ? (rawStatus as
        | "active"
        | "draft"
        | "pending"
        | "confirmed"
        | "needs_review")
    : "pending";
  return {
    id: String(raw.id ?? crypto.randomUUID()),
    source: String(raw.source ?? raw.source_node_id ?? raw.sourceId ?? ""),
    target: String(raw.target ?? raw.target_node_id ?? raw.targetId ?? ""),
    label: String(raw.label ?? raw.name ?? "") || undefined,
    kind:
      String(raw.kind ?? raw.relation_type ?? raw.type ?? "relationship") ||
      undefined,
    direction:
      raw.direction === "directed" || raw.directed === true
        ? "directed"
        : "undirected",
    status,
    note: String(raw.note ?? "") || undefined,
    source_refs: refs,
    relation_type:
      String(raw.relation_type ?? raw.kind ?? "related") || undefined,
    directed: raw.directed === undefined ? true : Boolean(raw.directed),
    weight: numberOrUndefined(raw.weight),
    data:
      raw.data && typeof raw.data === "object"
        ? (raw.data as Record<string, unknown>)
        : {},
    source_node_id: String(raw.source_node_id ?? raw.source ?? "") || undefined,
    target_node_id: String(raw.target_node_id ?? raw.target ?? "") || undefined,
    version: numberOrUndefined(raw.version),
  };
}

export function normalizeStoryGraph(value: unknown): StoryGraph {
  const raw = (unwrap<Record<string, unknown>>(value, [
    "story_graph",
    "storyGraph",
    "graph",
  ]) || {}) as Record<string, unknown>;
  const layout =
    raw.layout && typeof raw.layout === "object"
      ? (raw.layout as Record<string, unknown>)
      : {};
  const layoutJson =
    layout.layout_json && typeof layout.layout_json === "object"
      ? (layout.layout_json as Record<string, unknown>)
      : {};
  const layoutRows = Array.isArray(layoutJson.nodes)
    ? (layoutJson.nodes as unknown[])
    : Array.isArray(layoutJson.positions)
      ? (layoutJson.positions as unknown[])
      : [];
  const positions = new Map<string, { x: number; y: number }>();
  layoutRows.forEach((item) => {
    if (!item || typeof item !== "object") return;
    const row = item as Record<string, unknown>;
    const id = String(row.id ?? row.node_id ?? "");
    if (!id) return;
    const x = Number(
      row.x ??
        row.position_x ??
        (row.position && typeof row.position === "object"
          ? (row.position as Record<string, unknown>).x
          : 0),
    );
    const y = Number(
      row.y ??
        row.position_y ??
        (row.position && typeof row.position === "object"
          ? (row.position as Record<string, unknown>).y
          : 0),
    );
    positions.set(id, {
      x: Number.isFinite(x) ? x : 0,
      y: Number.isFinite(y) ? y : 0,
    });
  });
  const nodes = (
    Array.isArray(raw.nodes)
      ? raw.nodes.map(normalizeStoryGraphNode).filter(Boolean)
      : []
  ) as StoryGraphNode[];
  nodes.forEach((node) => {
    const position = positions.get(node.id);
    if (position) node.position = position;
  });
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = (
    Array.isArray(raw.edges) ? raw.edges.map(normalizeStoryGraphEdge) : []
  ).filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  return {
    nodes,
    edges,
    version: numberOrUndefined(raw.version ?? raw.revision ?? layout.version),
    layout_version: numberOrUndefined(layout.version),
    updated_at: String(raw.updated_at ?? raw.updatedAt ?? "") || undefined,
  };
}

function normalizeAgentTarget(value: unknown): AgentTarget {
  const raw = (value || {}) as Record<string, unknown>;
  const rawType = String(
    raw.type ?? raw.target_type ?? raw.targetType ?? "project",
  ).toLowerCase();
  const type =
    rawType.includes("character") || rawType === "person"
      ? "character"
      : rawType.includes("chapter") || rawType.includes("selection")
        ? "chapter"
        : rawType.includes("thread") || rawType.includes("plot")
          ? "thread"
          : rawType.includes("relation") || rawType.includes("edge")
            ? "relationship"
            : "project";
  return {
    type,
    id: String(raw.id ?? raw.target_id ?? raw.targetId ?? ""),
    chapter_id:
      raw.chapter_id === null
        ? null
        : String(raw.chapter_id ?? raw.chapterId ?? "") || undefined,
  } as AgentTarget;
}

function normalizeAgentPatch(value: unknown): AgentPatch {
  const raw = (value || {}) as Record<string, unknown>;
  return {
    path: String(raw.path ?? raw.field ?? ""),
    value: raw.value,
    label: String(raw.label ?? raw.field_label ?? "") || undefined,
    source_refs: Array.isArray(raw.source_refs ?? raw.sourceRefs)
      ? ((raw.source_refs ?? raw.sourceRefs) as unknown[]).map(
          normalizeSourceRef,
        )
      : [],
    confidence: numberOrUndefined(raw.confidence),
  };
}

function normalizeAssistantMessage(value: unknown): AssistantMessage {
  const raw = (value || {}) as Record<string, unknown>;
  const role = String(raw.role ?? "assistant");
  return {
    id: String(raw.id ?? raw.message_id ?? crypto.randomUUID()),
    role: role === "user" || role === "system" ? role : "assistant",
    content: String(raw.content ?? raw.text ?? ""),
    status: String(raw.status ?? "") || undefined,
    target:
      raw.target_json || raw.target
        ? normalizeAgentTarget(raw.target_json ?? raw.target)
        : undefined,
    context_snapshot:
      raw.context_snapshot && typeof raw.context_snapshot === "object"
        ? (raw.context_snapshot as AgentContextSnapshot)
        : undefined,
    authorized_asset_ids: Array.isArray(raw.authorized_asset_ids)
      ? (raw.authorized_asset_ids as unknown[]).map(String)
      : [],
    created_at: String(raw.created_at ?? raw.createdAt ?? "") || undefined,
    proposal_ids: Array.isArray(raw.proposal_ids ?? raw.proposalIds)
      ? ((raw.proposal_ids ?? raw.proposalIds) as unknown[]).map(String)
      : [],
  };
}

function normalizeAssistantProposal(value: unknown): AssistantProposal {
  const raw = (value || {}) as Record<string, unknown>;
  const rawStatus = String(raw.status ?? "proposed");
  const status = (
    ["proposed", "applying", "applied", "rejected"] as const
  ).includes(rawStatus as AssistantProposal["status"])
    ? (rawStatus as AssistantProposal["status"])
    : "proposed";
  const patchJson =
    raw.patch_json && typeof raw.patch_json === "object"
      ? (raw.patch_json as Record<string, unknown>)
      : {};
  const rawPatches = raw.patches ?? raw.operations ?? patchJson.patches;
  const patches = Array.isArray(rawPatches)
    ? (rawPatches as unknown[]).map(normalizeAgentPatch)
    : Object.entries(patchJson)
        .filter(([key]) => key !== "patches")
        .map(([path, patchValue]) =>
          normalizeAgentPatch({ path, value: patchValue }),
        );
  const operation = String(raw.operation ?? "").trim();
  const operationLabel = operation.replaceAll("_", " ").trim();
  return {
    id: String(raw.id ?? raw.proposal_id ?? crypto.randomUUID()),
    conversation_id: String(raw.conversation_id ?? raw.conversationId ?? ""),
    target: normalizeAgentTarget(
      raw.target ?? { target_type: raw.target_type, target_id: raw.target_id },
    ),
    summary: String(
      raw.summary ??
        raw.description ??
        raw.reason ??
        (operationLabel ? `建议${operationLabel}` : "待应用的设定提案"),
    ),
    patches,
    status,
    created_at: String(raw.created_at ?? raw.createdAt ?? "") || undefined,
    // Keep the canonical operation token for graph/character routing.  The
    // summary above is the human-readable form shown in the proposal card.
    operation: operation || undefined,
    target_type: String(raw.target_type ?? raw.targetType ?? "") || undefined,
    target_id:
      raw.target_id === null
        ? null
        : String(raw.target_id ?? raw.targetId ?? "") || undefined,
    change_set_id:
      String(raw.change_set_id ?? raw.changeSetId ?? "") || undefined,
    base_version:
      raw.base_version === null
        ? null
        : numberOrUndefined(raw.base_version ?? raw.baseVersion),
    base_memory_epoch:
      raw.base_memory_epoch === null
        ? null
        : numberOrUndefined(raw.base_memory_epoch ?? raw.baseMemoryEpoch),
    reason: String(raw.reason ?? "") || undefined,
  };
}

function normalizeAssistantConversation(
  value: unknown,
  projectId = "",
): AssistantConversation {
  const raw = (unwrap<Record<string, unknown>>(value, [
    "conversation",
    "session",
  ]) || {}) as Record<string, unknown>;
  return {
    id: String(raw.id ?? raw.conversation_id ?? crypto.randomUUID()),
    project_id: String(raw.project_id ?? raw.projectId ?? projectId),
    target: normalizeAgentTarget(raw.target),
    status: String(raw.status ?? "idle") as AssistantConversation["status"],
    messages: Array.isArray(raw.messages)
      ? raw.messages.map(normalizeAssistantMessage)
      : [],
    proposals: Array.isArray(raw.proposals)
      ? raw.proposals.map(normalizeAssistantProposal)
      : [],
    title: String(raw.title ?? "") || undefined,
    purpose: String(raw.purpose ?? "") || undefined,
    version: numberOrUndefined(raw.version),
    provider_profile_id:
      raw.provider_profile_id === null
        ? null
        : String(raw.provider_profile_id ?? "") || undefined,
    provider_name:
      String(
        raw.provider_name ??
          (raw.provider as Record<string, unknown> | undefined)?.name ??
          "",
      ) || undefined,
    provider_capabilities: normalizeProviderCapabilities(
      raw.provider_capabilities ??
        (raw.provider as Record<string, unknown> | undefined)?.capabilities,
    ),
    updated_at: String(raw.updated_at ?? raw.updatedAt ?? "") || undefined,
  };
}

export function normalizeAssistantRun(value: unknown): AssistantRun {
  const raw = (unwrap<Record<string, unknown>>(value, ["run", "data"]) ||
    {}) as Record<string, unknown>;
  return {
    id: String(raw.id ?? raw.run_id ?? ""),
    project_id: String(raw.project_id ?? ""),
    conversation_id: String(raw.conversation_id ?? ""),
    message_id:
      raw.message_id === null
        ? null
        : String(raw.message_id ?? "") || undefined,
    status: String(raw.status ?? "queued"),
    stage: String(raw.stage ?? "") || undefined,
    provider_profile_id:
      raw.provider_profile_id === null
        ? null
        : String(raw.provider_profile_id ?? "") || undefined,
    error: raw.error === null ? null : String(raw.error ?? "") || undefined,
    attempt: numberOrUndefined(raw.attempt ?? raw.attempts),
    created_at: String(raw.created_at ?? "") || undefined,
    started_at:
      raw.started_at === null
        ? null
        : String(raw.started_at ?? "") || undefined,
    finished_at:
      raw.finished_at === null
        ? null
        : String(raw.finished_at ?? "") || undefined,
  };
}

function mergeProposalEventMetadata(
  proposal: Record<string, unknown>,
  ...envelopes: Record<string, unknown>[]
): Record<string, unknown> {
  const merged = { ...proposal };
  // The SSE wire format carries durable event metadata beside the nested
  // proposal. Older workers omitted operation/target/version from the nested
  // object, so fill only missing values and keep proposal fields authoritative.
  for (const envelope of envelopes) {
    for (const [key, value] of Object.entries(envelope)) {
      if (key === "proposal" || value === undefined) continue;
      const current = merged[key];
      if (current === undefined || current === null || current === "") {
        merged[key] = value;
      }
    }
  }
  return merged;
}

export function normalizeAssistantEvent(value: unknown): AssistantEvent {
  const raw = (value || {}) as Record<string, unknown>;
  const sequence = Number(raw.sequence ?? raw.seq ?? 0);
  const payload =
    raw.payload_json && typeof raw.payload_json === "object"
      ? (raw.payload_json as Record<string, unknown>)
      : raw.payload && typeof raw.payload === "object"
        ? (raw.payload as Record<string, unknown>)
        : raw;
  const eventName = String(raw.event_type ?? raw.type ?? "").toLowerCase();
  const type = eventName.replace(/[.\-]/g, "_");
  const meta = {
    sequence,
    run_id: String(raw.run_id ?? payload.run_id ?? "") || undefined,
    attempt: numberOrUndefined(raw.attempt ?? payload.attempt),
    target:
      raw.target || payload.target
        ? normalizeAgentTarget(raw.target ?? payload.target)
        : undefined,
    base_version:
      (raw.base_version ?? payload.base_version) === null
        ? null
        : numberOrUndefined(raw.base_version ?? payload.base_version),
    cursor:
      String((raw.cursor ?? payload.cursor ?? sequence) || "") || undefined,
    retryable:
      typeof (payload.retryable ?? raw.retryable) === "boolean"
        ? Boolean(payload.retryable ?? raw.retryable)
        : undefined,
  };
  if (type === "message_delta" || type === "message_stream_delta") {
    return {
      ...meta,
      type: "message_delta",
      message_id: String(payload.message_id ?? payload.messageId ?? ""),
      delta: String(payload.delta ?? payload.content ?? ""),
    };
  }
  if (type === "message_replace") {
    return {
      ...meta,
      type: "message_replace",
      message_id: String(payload.message_id ?? payload.messageId ?? ""),
      content: String(
        payload.content ?? payload.reply ?? payload.message ?? "",
      ),
    };
  }
  if (type === "message_completed") {
    return {
      ...meta,
      type: "message_completed",
      message_id: String(payload.message_id ?? payload.messageId ?? ""),
      reply: String(payload.reply ?? payload.content ?? ""),
      proposal_count: numberOrUndefined(payload.proposal_count),
    };
  }
  if (type === "proposal_created") {
    const nestedProposal =
      payload.proposal && typeof payload.proposal === "object"
        ? (payload.proposal as Record<string, unknown>)
        : null;
    const proposalInput = nestedProposal
      ? mergeProposalEventMetadata(nestedProposal, raw, payload)
      : payload;
    return {
      ...meta,
      type: "proposal_created",
      proposal: normalizeAssistantProposal(proposalInput),
    };
  }
  if (type === "proposal_patch") {
    return {
      ...meta,
      type,
      proposal_id: String(payload.proposal_id ?? payload.proposalId ?? ""),
      patch: normalizeAgentPatch(payload.patch ?? payload),
    };
  }
  if (type === "proposal_completed" || type === "proposal_ready") {
    return {
      ...meta,
      type: "proposal_completed",
      proposal_id: String(payload.proposal_id ?? payload.proposalId ?? ""),
    };
  }
  if (type === "run_failed" || type === "error")
    return {
      ...meta,
      type: "error",
      retryable: meta.retryable ?? true,
      message: String(payload.message ?? payload.error ?? "Agent 暂时不可用"),
    };
  const rawStatus =
    type === "run_started" || type === "message_started"
      ? "streaming"
      : type === "run_completed"
        ? "idle"
        : type === "run_stage"
          ? String(
              payload.status ??
                (payload.stage === "queued" ? "queued" : "streaming"),
            )
          : String(payload.status ?? "idle");
  const allowedStatuses = [
    "idle",
    "queued",
    "running",
    "streaming",
    "reconnecting",
    "applying",
    "applied",
    "error",
    "disconnected",
    "cancelled",
  ] as const;
  const status = allowedStatuses.includes(
    rawStatus as (typeof allowedStatuses)[number],
  )
    ? (rawStatus as (typeof allowedStatuses)[number])
    : "idle";
  return {
    ...meta,
    type: "status",
    status,
    stage: String(payload.stage ?? "") || undefined,
    message: String(payload.message ?? payload.reply ?? "") || undefined,
  };
}

function strictCapabilityBoolean(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (value === 1) return true;
    if (value === 0) return false;
    return undefined;
  }
  if (typeof value !== "string") return undefined;
  const normalized = value.trim().toLowerCase();
  if (["true", "1", "yes", "on", "enabled"].includes(normalized)) return true;
  if (["false", "0", "no", "off", "disabled", ""].includes(normalized))
    return false;
  return undefined;
}

export function normalizeProviderCapabilities(
  value: unknown,
): Record<string, boolean> {
  const raw =
    value && typeof value === "object"
      ? (value as Record<string, unknown>)
      : {};
  const normalized: Record<string, boolean> = {};
  for (const [key, item] of Object.entries(raw)) {
    if (
      ["vision", "image_input", "supports_vision", "multimodal"].includes(key)
    )
      continue;
    const parsed = strictCapabilityBoolean(item);
    if (parsed !== undefined) normalized[key] = parsed;
  }
  // A canonical explicit false is authoritative. Legacy aliases are consulted
  // only when vision is absent or malformed.
  const canonical = strictCapabilityBoolean(raw.vision);
  const legacy = [raw.image_input, raw.supports_vision, raw.multimodal]
    .map(strictCapabilityBoolean)
    .find((item) => item !== undefined);
  normalized.vision = canonical ?? legacy ?? false;
  return normalized;
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
    api_version:
      String(source.api_version ?? source.apiVersion ?? "") || undefined,
    max_output_tokens: numberOrUndefined(
      source.max_output_tokens ?? source.maxOutputTokens,
    ),
    anthropic_workspace_id:
      String(
        source.anthropic_workspace_id ?? source.anthropicWorkspaceId ?? "",
      ) || undefined,
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
    capabilities: normalizeProviderCapabilities(source.capabilities),
    enabled: source.enabled === undefined ? true : Boolean(source.enabled),
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
