import type { Page, Request, Route } from "@playwright/test";

type JsonRecord = Record<string, unknown>;

export interface MockRequest {
  method: string;
  path: string;
  body: unknown;
}

export interface MockStoryApiOptions {
  /** Projects returned by the first library request. An empty list is used by default. */
  initialProjects?: Array<JsonRecord>;
  /** Optional proposal emitted by the mocked Agent after the next message. */
  assistantProposal?: JsonRecord;
  /** Optional proposal sequence emitted as one live Agent build. */
  assistantProposals?: JsonRecord[];
  /** Optional wait before the first live Agent event, used to assert thinking UI. */
  assistantEventDelayMs?: number;
  /** Keep the mocked run active so stop/resume controls can be exercised. */
  assistantHoldRun?: boolean;
}

export interface MockStoryApiState {
  requests: MockRequest[];
  projects: Array<JsonRecord>;
  createdProjectIds: string[];
  graphSaveCount: number;
}

interface ProjectData {
  project: JsonRecord;
  chapters: JsonRecord[];
  characters: JsonRecord[];
  canon: JsonRecord[];
  graph: JsonRecord;
  threads: JsonRecord[];
  conversation: JsonRecord | null;
  messages: JsonRecord[];
  proposals: JsonRecord[];
}

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

const now = "2026-09-01T00:00:00.000Z";

export const storyProjectFixture: JsonRecord = {
  id: "project-1",
  title: "雾中灯塔",
  logline: "雾港里的守塔人必须查明一束不该出现的灯光。",
  genre: "悬疑 / 奇幻",
  tone: "克制、具体、留白",
  current_chapter_id: "chapter-1",
  canon_version: 2,
  memory_epoch: 3,
  needs_rebuild: false,
  summary_status: "current",
  updated_at: now,
  source: "local",
};

export const emptyStoryProjectFixture: JsonRecord = {
  ...storyProjectFixture,
  id: "project-empty",
  title: "空白灯塔",
  current_chapter_id: null,
  canon_version: 0,
  memory_epoch: 0,
  summary_status: "not_started",
};

function makeChapter(projectId: string, id: string, title = "第一章 · 灯塔亮起") {
  const content = "林渡在雾里看见灯塔亮起，决定查明这束不该出现的光。";
  return {
    id,
    project_id: projectId,
    chapter_number: 1,
    sort_order: 0,
    number: 1,
    title,
    status: "confirmed",
    content,
    word_count: content.length,
    revision_id: "revision-1",
    summary: "林渡发现异常灯光并决定追查。",
    summary_status: "current",
    updated_at: now,
  } satisfies JsonRecord;
}

function makeCharacters(projectId: string) {
  return [
    {
      id: "char-1",
      project_id: projectId,
      name: "林渡",
      aliases: ["守塔人"],
      role: "主角",
      age: "28",
      pronouns: "她",
      appearance: "总穿一件旧雨衣。",
      personality: "冷静、敏锐。",
      background: "在雾港长大。",
      goals: "查明灯塔秘密",
      motivation: "守住不该被遗忘的真相",
      conflict_fears: "害怕再次失去家人",
      tags: ["主角", "灯塔"],
      custom_fields: {},
      status: "active",
      version: 1,
      portrait: {
        id: "asset-1",
        project_id: projectId,
        url: "/mock/portrait.png",
        alt: "林渡的人物肖像",
        content_type: "image/png",
      },
      image_media_id: "asset-1",
      source_refs: [],
      updated_at: now,
    },
    {
      id: "char-2",
      project_id: projectId,
      name: "闻潮",
      aliases: [],
      role: "灯塔管理员",
      personality: "寡言、守约。",
      goals: "掩藏灯塔的旧记录",
      tags: ["配角"],
      custom_fields: {},
      status: "confirmed",
      version: 1,
      source_refs: [],
      updated_at: now,
    },
  ] satisfies JsonRecord[];
}

function makeGraph(projectId: string, chapterId?: string): JsonRecord {
  return {
    project_id: projectId,
    chapter_id: chapterId || null,
    version: 1,
    updated_at: now,
    nodes: [
      {
        id: "node-char-1",
        scope_chapter_id: chapterId || null,
        node_type: "character",
        character_id: "char-1",
        ref_id: "char-1",
        label: "林渡",
        position: { x: 80, y: 80 },
        status: "active",
        version: 1,
      },
      {
        id: "node-thread-1",
        scope_chapter_id: chapterId || null,
        node_type: "plot",
        plot_thread_id: "thread-1",
        ref_id: "thread-1",
        label: "灯塔秘密",
        position: { x: 360, y: 80 },
        status: "active",
        version: 1,
      },
    ],
    edges: [
      {
        id: "edge-1",
        scope_chapter_id: chapterId || null,
        source_node_id: "node-char-1",
        target_node_id: "node-thread-1",
        source: "node-char-1",
        target: "node-thread-1",
        relation_type: "追查",
        kind: "追查",
        label: "追查",
        directed: true,
        status: "active",
        version: 1,
      },
    ],
  };
}

function makeProjectData(project: JsonRecord): ProjectData {
  const projectId = String(project.id);
  const chapterId = String(project.current_chapter_id || "");
  const chapters = chapterId ? [makeChapter(projectId, chapterId)] : [];
  const threads = [
    {
      id: "thread-1",
      project_id: projectId,
      title: "灯塔秘密",
      kind: "main",
      status: "active",
      next_beat: "继续追查灯光来源",
      points: [],
    },
  ];
  return {
    project,
    chapters,
    characters: makeCharacters(projectId),
    canon: [],
    graph: makeGraph(projectId, chapterId || undefined),
    threads,
    conversation: null,
    messages: [],
    proposals: [],
  };
}

function makeProject(id: string, title: string, startMode: string): JsonRecord {
  const hasBlankChapter = startMode === "blank";
  return {
    id,
    title,
    logline: "",
    genre: "悬疑 / 奇幻",
    tone: "克制、具体、留白",
    current_chapter_id: hasBlankChapter ? `${id}-chapter-1` : null,
    canon_version: 0,
    memory_epoch: 0,
    needs_rebuild: false,
    summary_status: "not_started",
    updated_at: now,
    source: startMode === "import" ? "imported" : "local",
  };
}

function readBody(request: Request): unknown {
  try {
    return request.postDataJSON();
  } catch {
    return request.postData() || undefined;
  }
}

function bodyRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

async function json(route: Route, payload: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  });
}

function assistantConversation(data: ProjectData, projectId: string) {
  return {
    ...(data.conversation || {}),
    id: data.conversation?.id || "assistant-1",
    project_id: projectId,
    target: data.conversation?.target || { type: "project", id: projectId },
    status: "idle",
    title: String(data.conversation?.title || "故事设定助手"),
    purpose: String(data.conversation?.purpose || "setup_character"),
    apply_mode: String(data.conversation?.apply_mode || "preview"),
    version: Number(data.conversation?.version || 1),
    messages: clone(data.messages),
    proposals: clone(data.proposals),
    updated_at: now,
  } satisfies JsonRecord;
}

function sseEvent(sequence: number, eventType: string, payload: JsonRecord) {
  return `id: ${sequence}\ndata: ${JSON.stringify({ sequence, event_type: eventType, payload_json: payload })}\n\n`;
}

async function assistantEvents(
  route: Route,
  data: ProjectData,
  delayMs = 0,
  holdRun = false,
) {
  if (delayMs > 0) {
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  const messageId = "assistant-message-1";
  if (holdRun) {
    const cancelled = data.conversation?.run_cancelled === true;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "cache-control": "no-cache" },
      body: sseEvent(1, cancelled ? "run.cancelled" : "run.started", {
        run_id: "assistant-run-1",
        status: cancelled ? "cancelled" : "running",
        stage: cancelled ? "cancelled" : "calling_model",
        ...(cancelled ? { message: "已停止本次任务，已生成的内容仍保留在对话中。" } : {}),
      }),
    });
    return;
  }
  const proposals = data.proposals.length
    ? data.proposals
    : [
        {
          id: "proposal-1",
          conversation_id: "assistant-1",
          target: { type: "character", id: "char-1" },
          summary: "补充人物动机",
          patches: [
            { path: "motivation", value: "守护灯塔", label: "深层动机" },
          ],
          status: "proposed",
        },
      ];
  let sequence = 1;
  const frames = [
    sseEvent(sequence++, "message.delta", {
      message_id: messageId,
      delta: "已记录。",
    }),
  ];
  for (const proposal of proposals) {
    const proposalId = String(proposal.id || `proposal-${sequence}`);
    const proposalPatches = Array.isArray(proposal.patches)
      ? (proposal.patches as JsonRecord[])
      : [];
    frames.push(
      sseEvent(sequence++, "proposal.created", {
        proposal: { ...clone(proposal), patches: [], status: "building" },
      }),
    );
    for (const patch of proposalPatches) {
      frames.push(
        sseEvent(sequence++, "proposal.patch", {
          proposal_id: proposalId,
          patch,
        }),
      );
    }
    frames.push(
      sseEvent(sequence++, "proposal.completed", { proposal_id: proposalId }),
    );
  }
  frames.push(
    sseEvent(sequence, "message.completed", {
      message_id: messageId,
      reply: "已整理人物设定。",
      proposal_count: proposals.length,
    }),
  );
  const body = frames.join("");
  await route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    headers: { "cache-control": "no-cache", connection: "keep-alive" },
    body,
  });
}

/**
 * Install an in-browser API double for the product's core acceptance flows.
 * The state object intentionally exposes only request observations, so specs
 * can assert user-visible actions without coupling to implementation details.
 */
export async function mockStoryApi(
  page: Page,
  options: MockStoryApiOptions = {},
): Promise<MockStoryApiState> {
  const initialProjects = (options.initialProjects || []).map(clone);
  const state: MockStoryApiState = {
    requests: [],
    projects: initialProjects,
    createdProjectIds: [],
    graphSaveCount: 0,
  };
  const dataByProject = new Map<string, ProjectData>();
  initialProjects.forEach((project) => dataByProject.set(String(project.id), makeProjectData(project)));
  let projectSequence = 0;
  let memoryRun: JsonRecord | null = null;
  let provider: JsonRecord = {
    id: "provider-1",
    name: "Mock Provider",
    base_url: "http://mock",
    protocol: "chat_completions",
    default_model: "mock-model",
    context_length: 8192,
    capabilities: { vision: true, image_input: true, tools: true },
    enabled: true,
    is_default: true,
    api_key_set: true,
  };

  const getData = (projectId: string) => {
    const existing = dataByProject.get(projectId);
    if (existing) return existing;
    const project = makeProject(projectId, "未命名小说", "setup");
    const data = makeProjectData(project);
    dataByProject.set(projectId, data);
    return data;
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const method = request.method().toUpperCase();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "") || "/";
    const body = readBody(request);
    state.requests.push({ method, path, body });
    const parts = path.split("/").filter(Boolean);

    if (method === "OPTIONS") {
      await json(route, {}, 204);
      return;
    }
    if (path === "/auth/config" && method === "GET") {
      await json(route, {
        mode: "username",
        verification_required: false,
        password_reset_available: false,
      });
      return;
    }
    if (path === "/auth/me" && method === "GET") {
      await json(route, {
        user: {
          id: "user-1",
          email: "writer@example.test",
          display_name: "测试作者",
          is_email_verified: true,
          is_active: true,
          default_provider_id: "provider-1",
        },
        csrf_token: "test-csrf",
      });
      return;
    }
    if (path === "/auth/change-password" && method === "POST") {
      await json(route, {
        user: {
          id: "user-1",
          email: "writer@example.test",
          display_name: "测试作者",
          is_email_verified: true,
          is_active: true,
          default_provider_id: "provider-1",
        },
        csrf_token: "test-csrf",
      });
      return;
    }
    if (["/auth/logout", "/auth/logout-all"].includes(path) && method === "POST") {
      await route.fulfill({ status: 204 });
      return;
    }
    if (path === "/auth/account" && method === "DELETE") {
      await route.fulfill({ status: 204 });
      return;
    }
    if (path === "/preferences" || path === "/account/preferences") {
      if (method === "PATCH") {
        await json(route, { auto_summary_enabled: Boolean(bodyRecord(body).auto_summary_enabled), default_start_mode: "blank", preferences_version: 2, updated_at: now });
      } else {
        await json(route, { auto_summary_enabled: true, default_start_mode: "blank", preferences_version: 1, updated_at: now });
      }
      return;
    }
    if (path === "/providers" && method === "GET") {
      await json(route, { providers: [clone(provider)] });
      return;
    }
    if (parts[0] === "memory-runs" && parts[1]) {
      if (parts[2] === "events" && method === "GET") {
        const run = memoryRun || {
          id: parts[1],
          project_id: "project-1",
          scope: "project",
          status: "running",
          stage: "project:compose",
          progress: 48,
          phase_label: "正在编排 6000–8000 字全书记忆",
        };
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          headers: { "cache-control": "no-cache", connection: "keep-alive" },
          body: `event: progress\ndata: ${JSON.stringify(run)}\n\n`,
        });
        return;
      }
      if (method === "GET") {
        await json(route, memoryRun || { id: parts[1], status: "running", progress: 48 });
        return;
      }
    }
    if (path === "/providers" && method === "POST") {
      const input = bodyRecord(body);
      provider = {
        ...provider,
        ...input,
        id: "provider-created",
        enabled: true,
        is_default: false,
        api_key_set: Boolean(input.api_key),
      };
      await json(route, clone(provider), 201);
      return;
    }
    if (path === "/providers/test" && method === "POST") {
      const input = bodyRecord(body);
      await json(route, { ok: true, model: input.default_model || "mock-model" });
      return;
    }
    if (path === "/providers/default" && method === "GET") {
      await json(route, clone(provider));
      return;
    }
    if (
      parts[0] === "providers" &&
      parts.length === 3 &&
      parts[2] === "test" &&
      method === "POST"
    ) {
      await json(route, { ok: true, model: provider.default_model || "mock-model" });
      return;
    }
    if (
      parts[0] === "providers" &&
      parts.length === 3 &&
      parts[2] === "key" &&
      method === "DELETE"
    ) {
      provider = { ...provider, api_key_set: false };
      await route.fulfill({ status: 204 });
      return;
    }
    if (
      parts[0] === "providers" &&
      parts.length === 3 &&
      parts[2] === "default" &&
      method === "PUT"
    ) {
      provider = { ...provider, is_default: true };
      await json(route, clone(provider));
      return;
    }
    if (parts[0] === "providers" && parts.length === 2 && method === "PATCH") {
      const input = bodyRecord(body);
      provider = {
        ...provider,
        ...input,
        capabilities:
          input.capabilities && typeof input.capabilities === "object"
            ? clone(input.capabilities)
            : provider.capabilities,
      };
      await json(route, clone(provider));
      return;
    }
    if (parts[0] === "providers" && parts.length === 2 && method === "DELETE") {
      await route.fulfill({ status: 204 });
      return;
    }
    if (path === "/projects" && method === "GET") {
      await json(route, { projects: clone(state.projects) });
      return;
    }
    if (path === "/projects" && method === "POST") {
      const input = bodyRecord(body);
      const startMode = String(input.start_mode || "blank");
      projectSequence += 1;
      const project = makeProject(`project-created-${projectSequence}`, String(input.title || input.name || "未命名小说"), startMode);
      project.logline = String(input.logline || input.description || "");
      project.genre = String(input.genre || project.genre);
      project.tone = String(input.tone || project.tone);
      const data = makeProjectData(project);
      if (startMode === "blank") data.chapters = [makeChapter(String(project.id), String(project.current_chapter_id), String(input.first_chapter_title || "第一章 · 未命名稿纸"))];
      dataByProject.set(String(project.id), data);
      state.projects.push(project);
      state.createdProjectIds.push(String(project.id));
      await json(route, project, 201);
      return;
    }

    if (parts[0] === "projects" && parts.length >= 2) {
      const projectId = parts[1];
      if (parts.length === 2 && method === "DELETE") {
        const exists = state.projects.some((project) => project.id === projectId);
        if (!exists) {
          await json(route, { detail: "project not found" }, 404);
          return;
        }
        state.projects = state.projects.filter((project) => project.id !== projectId);
        dataByProject.delete(projectId);
        await route.fulfill({ status: 204 });
        return;
      }
      const data = getData(projectId);
      if (parts.length === 2 && method === "GET") {
        await json(route, data.project);
        return;
      }
      if (parts.length === 2 && method === "PATCH") {
        const input = bodyRecord(body);
        if (input.name !== undefined) data.project.title = input.name;
        if (input.description !== undefined) data.project.logline = input.description;
        Object.assign(data.project, input);
        await json(route, data.project);
        return;
      }
      if (parts[2] === "chapters") {
        if (parts.length === 3 && method === "GET") {
          await json(route, { chapters: clone(data.chapters) });
          return;
        }
        if (parts.length === 3 && method === "POST") {
          const input = bodyRecord(body);
          const next = data.chapters.length + 1;
          const chapter = makeChapter(projectId, `${projectId}-chapter-${next}`, String(input.title || `第${next}章 · 未命名稿纸`));
          Object.assign(chapter, input, { id: chapter.id, project_id: projectId, chapter_number: Number(input.chapter_number || next), number: Number(input.chapter_number || next) });
          data.chapters.push(chapter);
          await json(route, chapter, 201);
          return;
        }
      }
      if (parts[2] === "import" && parts[3] === "preview" && method === "POST") {
        await json(route, {
          file_name: "旧稿.txt",
          file_hash: "mock-import-hash",
          source_hash: "mock-import-hash",
          encoding: "UTF-8",
          warnings: [],
          chapters: [
            {
              key: "import-1",
              number: 1,
              title: "第一章 · 旧港",
              content: "潮声漫过旧港。",
              selected: true,
              source_start: 0,
              source_end: 8,
            },
          ],
        });
        return;
      }
      if (parts[2] === "import" && parts[3] === "commit" && method === "POST") {
        const input = bodyRecord(body);
        const rows = Array.isArray(input.chapters) ? input.chapters : [];
        const imported = rows.map((row, index) => {
          const item = bodyRecord(row);
          return makeChapter(
            projectId,
            `${projectId}-import-${index + 1}`,
            String(item.title || `导入章节 ${index + 1}`),
          );
        });
        data.chapters.push(...imported);
        await json(route, { chapters: imported }, 201);
        return;
      }
      if (parts[2] === "canon" && method === "GET") {
        await json(route, { canon_items: clone(data.canon) });
        return;
      }
      if (parts[2] === "story-map" && method === "GET") {
        await json(route, { chapters: clone(data.chapters), canon_items: clone(data.canon), threads: clone(data.threads), timeline: [], graph: clone(data.graph) });
        return;
      }
      if (parts[2] === "characters") {
        if (parts.length === 3 && method === "GET") {
          await json(route, { characters: clone(data.characters) });
          return;
        }
        if (parts.length === 3 && method === "POST") {
          const input = bodyRecord(body);
          const character = { id: `${projectId}-char-${data.characters.length + 1}`, project_id: projectId, aliases: [], tags: [], custom_fields: {}, status: "draft", source_refs: [], version: 1, ...input };
          data.characters.push(character);
          await json(route, character, 201);
          return;
        }
        if (parts.length === 4 && method === "PATCH") {
          const character = data.characters.find((item) => item.id === parts[3]);
          if (!character) { await json(route, { detail: "character not found" }, 404); return; }
          Object.assign(character, bodyRecord(body), { id: character.id, project_id: projectId });
          await json(route, character);
          return;
        }
        if (parts.length === 4 && method === "DELETE") {
          data.characters = data.characters.filter((item) => item.id !== parts[3]);
          await json(route, {}, 204);
          return;
        }
      }
      if (parts[2] === "story-graph") {
        if (parts.length === 3 && method === "GET") {
          const graph = clone(data.graph);
          graph.chapter_id = url.searchParams.get("chapter_id") || null;
          await json(route, graph);
          return;
        }
        if (parts[3] === "nodes" && method === "POST") {
          state.graphSaveCount += 1;
          const input = bodyRecord(body);
          const node = { id: `${projectId}-node-${data.graph.nodes ? (data.graph.nodes as unknown[]).length + 1 : 1}`, ...input };
          (data.graph.nodes as JsonRecord[]).push(node);
          await json(route, node, 201);
          return;
        }
        if (parts[3] === "edges" && parts.length === 5) {
          state.graphSaveCount += 1;
          const edgeId = decodeURIComponent(parts[4]);
          const edge = (data.graph.edges as JsonRecord[]).find((item) => item.id === edgeId);
          if (!edge && method === "PATCH") { await json(route, { detail: "edge not found" }, 404); return; }
          if (method === "DELETE") {
            data.graph.edges = (data.graph.edges as JsonRecord[]).filter((item) => item.id !== edgeId);
            await json(route, {}, 204);
            return;
          }
          if (method === "PATCH" && edge) { Object.assign(edge, bodyRecord(body), { id: edge.id }); await json(route, edge); return; }
        }
        if (parts[3] === "edges" && parts.length === 4 && method === "POST") {
          state.graphSaveCount += 1;
          const input = bodyRecord(body);
          const edge = { id: `${projectId}-edge-${(data.graph.edges as unknown[]).length + 1}`, ...input };
          (data.graph.edges as JsonRecord[]).push(edge);
          await json(route, edge, 201);
          return;
        }
        if (parts[3] === "layout" && method === "PATCH") {
          state.graphSaveCount += 1;
          const input = bodyRecord(body);
          const layout = bodyRecord(input.layout_json);
          const rows = Array.isArray(layout.nodes) ? layout.nodes : [];
          for (const row of rows) {
            const item = bodyRecord(row);
            const node = (data.graph.nodes as JsonRecord[]).find((candidate) => candidate.id === item.id);
            if (node) node.position = { x: item.x, y: item.y };
          }
          await json(route, { layout_json: clone(layout), version: Number(data.graph.version || 1) });
          return;
        }
      }
      if (parts[2] === "memory" && method === "POST") {
        await json(route, { id: `memory-${projectId}`, project_id: projectId, status: "current", scope: bodyRecord(body).scope || "project", progress: 100 });
        return;
      }
      if (parts[2] === "generations" && parts[3] === "latest" && method === "GET") {
        await json(route, { detail: "no generation" }, 404);
        return;
      }
      if (parts[2] === "assistant") {
        const assistantPart = parts[3];
        if (assistantPart === "conversations" && parts.length === 4 && method === "GET") {
          await json(route, { conversations: data.conversation ? [assistantConversation(data, projectId)] : [] });
          return;
        }
        if (assistantPart === "conversations" && parts.length === 4 && method === "POST") {
          const input = bodyRecord(body);
          data.conversation = {
            id: "assistant-1",
            project_id: projectId,
            target: input.target || { type: "project", id: projectId },
            title: String(input.title || "新的写作对话"),
            purpose: String(input.purpose || "chapter"),
            status: "idle",
            version: 1,
          };
          data.messages = [];
          data.proposals = [];
          await json(route, assistantConversation(data, projectId), 201);
          return;
        }
        if (assistantPart === "conversations" && parts.length >= 5) {
          const conversationId = parts[4];
          if (parts.length === 5 && method === "GET") {
            await json(route, assistantConversation(data, projectId));
            return;
          }
          if (parts[5] === "messages" && parts.length === 6 && method === "GET") {
            await json(route, { messages: clone(data.messages) });
            return;
          }
          if (parts[5] === "messages" && parts.length === 6 && method === "POST") {
            const input = bodyRecord(body);
            const content = String(input.content || "");
            const target = input.target || data.conversation?.target || { type: "project", id: projectId };
            const configuredProposals = options.assistantProposals
              ? options.assistantProposals
              : options.assistantProposal
                ? [options.assistantProposal]
                : [
                    {
                      id: "proposal-1",
                      target,
                      summary: "补充人物动机",
                      patches: [
                        { path: "motivation", value: "守护灯塔", label: "深层动机" },
                      ],
                      status: "proposed",
                    },
                  ];
            data.proposals = configuredProposals.map((proposal) => ({
              ...clone(proposal),
              conversation_id: conversationId,
              created_at: now,
            }));
            const proposalIds = data.proposals.map((proposal) => String(proposal.id));
            const turn = data.messages.filter((item) => item.role === "user").length + 1;
            const userMessage = { id: `user-message-${turn}`, role: "user", content, target, context_snapshot: input.context_snapshot || {}, authorized_asset_ids: input.authorized_asset_ids || [], proposal_ids: proposalIds, created_at: now };
            const assistantMessage = { id: `assistant-message-${turn}`, role: "assistant", content: "已整理人物设定。", proposal_ids: proposalIds, created_at: now };
            data.messages = [...data.messages, userMessage, assistantMessage];
            if (data.conversation) {
              if (["故事设定助手", "新的写作对话"].includes(String(data.conversation.title || ""))) {
                data.conversation.title = content.length > 22 ? `${content.slice(0, 22)}…` : content;
              }
              data.conversation.version = Number(data.conversation.version || 1) + 1;
            }
            await json(route, {
              conversation: assistantConversation(data, projectId),
              message: userMessage,
              run: { id: `assistant-run-${turn}`, status: "running", stage: "streaming" },
            }, 202);
            return;
          }
          if (parts[5] === "runs" && parts.length === 6 && method === "GET") {
            const turn = data.messages.filter((item) => item.role === "user").length;
            const cancelled = data.conversation?.run_cancelled === true;
            await json(route, {
              runs: turn
                ? [{ id: `assistant-run-${turn}`, status: cancelled ? "cancelled" : "completed", stage: cancelled ? "cancelled" : "completed" }]
                : [],
            });
            return;
          }
          if (parts[5] === "runs" && parts[7] === "cancel" && method === "POST") {
            if (data.conversation) data.conversation.run_cancelled = true;
            await json(route, {
              id: parts[6],
              project_id: projectId,
              conversation_id: conversationId,
              status: "cancelled",
              stage: "cancelled",
            });
            return;
          }
          if (parts[5] === "events" && parts[6] === "stream" && method === "GET") {
            await assistantEvents(
              route,
              data,
              options.assistantEventDelayMs,
              options.assistantHoldRun,
            );
            return;
          }
        }
        if (assistantPart === "proposals" && parts.length === 4 && method === "GET") {
          await json(route, { proposals: clone(data.proposals) });
          return;
        }
        if (
          assistantPart === "proposals" &&
          parts.length === 5 &&
          method === "POST" &&
          ["apply-batch", "reject-batch"].includes(parts[4])
        ) {
          const proposalIds = Array.isArray(bodyRecord(body).proposal_ids)
            ? (bodyRecord(body).proposal_ids as unknown[]).map(String)
            : [];
          const nextStatus = parts[4] === "apply-batch" ? "applied" : "rejected";
          const changed = data.proposals.filter((proposal) =>
            proposalIds.includes(String(proposal.id)),
          );
          changed.forEach((proposal) => {
            proposal.status = nextStatus;
          });
          if (
            nextStatus === "applied" &&
            String(data.conversation?.purpose || "") === "global"
          ) {
            memoryRun = {
              id: `memory-global-${projectId}`,
              project_id: projectId,
              scope: "project",
              status: "running",
              stage: "project:compose",
              progress: 48,
              phase_label: "正在编排 6000–8000 字全书记忆",
            };
          }
          await json(route, {
            status: nextStatus,
            proposals: clone(changed),
            memory_run: memoryRun ? clone(memoryRun) : null,
          });
          return;
        }
        if (assistantPart === "proposals" && parts.length === 5 && method === "PATCH") {
          const proposalId = parts[4];
          const proposal = data.proposals.find((item) => item.id === proposalId);
          if (!proposal) { await json(route, { detail: "proposal not found" }, 404); return; }
          const input = bodyRecord(body);
          const edits = Array.isArray(input.patches) ? input.patches.map(bodyRecord) : [];
          const proposalPatches = Array.isArray(proposal.patches)
            ? (proposal.patches as JsonRecord[])
            : [];
          for (const edit of edits) {
            const path = String(edit.path || "").replace(/^\//, "");
            const patch = proposalPatches.find(
              (candidate) => String(candidate.path || "").replace(/^\//, "") === path,
            );
            if (patch) patch.value = edit.value;
          }
          await json(route, proposal);
          return;
        }
        if (assistantPart === "proposals" && parts.length === 6 && method === "POST") {
          const proposalId = parts[4];
          const action = parts[5];
          const proposal = data.proposals.find((item) => item.id === proposalId);
          if (!proposal) { await json(route, { detail: "proposal not found" }, 404); return; }
          proposal.status = action === "apply" ? "applied" : "rejected";
          await json(route, proposal);
          return;
        }
      }
    }

    if (path.startsWith("/chapters/") && method === "PATCH") {
      const chapterId = parts[1];
      for (const data of dataByProject.values()) {
        const chapter = data.chapters.find((item) => item.id === chapterId);
        if (chapter) { Object.assign(chapter, bodyRecord(body)); await json(route, chapter); return; }
      }
    }
    if (path.startsWith("/chapters/") && parts[2] === "complete" && method === "POST") {
      await json(route, { chapter: { id: parts[1], status: "confirmed" }, memory_run: null, auto_summary_enabled: true });
      return;
    }
    if (path.startsWith("/media/") && method === "DELETE") {
      await json(route, {}, 204);
      return;
    }
    await json(route, {});
  });

  return state;
}
