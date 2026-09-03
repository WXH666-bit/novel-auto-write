import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AnimatePresence,
  LayoutGroup,
  motion,
  useReducedMotion,
} from "motion/react";
import {
  addEdge,
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  ArrowRight,
  BookOpenCheck,
  Bot,
  Check,
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileText,
  FileDiff,
  ImagePlus,
  Link2,
  Loader2,
  MessageCircle,
  Network,
  PencilLine,
  Plus,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  Square,
  Table2,
  Trash2,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import {
  applyAssistantProposals,
  cancelAssistantRun,
  createAssistantConversation,
  createChapter,
  createCharacter,
  deleteCharacterPortrait,
  getCharacter,
  getCharacters,
  getChapters,
  getAssistantConversation,
  getProjects,
  getStoryGraph,
  getStoryMap,
  listAssistantConversations,
  listAssistantMessages,
  listAssistantProposals,
  listAssistantRuns,
  listenAssistantEvents,
  apiErrorCode,
  retryAssistantRun,
  rejectAssistantProposals,
  saveStoryGraph,
  sendAssistantMessage,
  updateCharacter,
  uploadCharacterPortrait,
} from "./api";
import type {
  AgentPatch,
  AgentContextSnapshot,
  AgentTarget,
  AgentWorkMode,
  AssistantConversation,
  AssistantEvent,
  AssistantProposal,
  AssistantProposalStatus,
  AssistantProposalUpdatePatch,
  CharacterCard,
  Chapter,
  EntityViewMode,
  MemoryRun,
  ProjectMemory,
  Project,
  ProviderProfile,
  SourceRef,
  StoryGraph,
  StoryGraphEdge,
  StoryGraphNode,
  StoryMap,
  StudioMode,
} from "./types";

type AgentFieldDraft = {
  proposalId: string;
  label: string;
  status: AssistantProposalStatus;
  before: string;
  value: unknown;
  conflict: boolean;
};

type AgentDraftPatch = AssistantProposalUpdatePatch & {
  label?: string;
};

const AUTOMATIC_APPLY_PREVIEW_MS = 6000;

export type ChapterAgentDraft = {
  proposalId: string;
  summary: string;
  status: AssistantProposalStatus;
  before: string;
  after: string;
  replacement: string;
  editPath: string;
  editValue: string;
  editsWholeChapter: boolean;
  start: number;
  end: number;
};

type NoticeTone = "info" | "success" | "warning" | "error";

function isPreviewProposal(proposal: AssistantProposal) {
  return proposal.status === "building" || proposal.status === "proposed";
}

/**
 * Empty-state shortcuts should only be offered when their target exists.
 * Otherwise they encourage a vague request that the Agent cannot safely
 * attach to a character or a relationship graph.
 */
export function getAgentQuickPromptVisibility(
  target: Pick<AgentTarget, "type" | "id">,
  relationNodeCount: number,
) {
  return {
    motivation:
      target.type === "character" &&
      Boolean(target.id) &&
      target.id !== "new-character",
    tension: relationNodeCount >= 2,
  };
}

export function resolveAgentMessageTarget(
  target: AgentTarget,
  workMode: AgentWorkMode | "project",
  activeChapter: Pick<Chapter, "id"> | null,
): AgentTarget {
  if (workMode === "global") {
    return { type: "project", id: target.type === "project" ? target.id : "", chapter_id: null };
  }
  // An entity selected in the people/graph surfaces remains useful context,
  // while the selected chapter is attached automatically.  There is no
  // selection-only mode anymore.
  if (["character", "thread", "relationship"].includes(target.type)) {
    return { ...target, chapter_id: activeChapter?.id || null };
  }
  if (workMode !== "project" && activeChapter) {
    return {
      type: "chapter",
      id: activeChapter.id,
      chapter_id: activeChapter.id,
    };
  }
  return target.type === "project" ? { ...target, chapter_id: null } : target;
}

export function shouldAutoCreateChapterDraft(message: string): boolean {
  const value = message.trim();
  if (!value) return false;
  const writingAction = /(写|创作|续写|扩写|改写|重写|起草|生成|仿写)/;
  const chapterObject = /(第\s*[一二三四五六七八九十百千万零〇两\d]+\s*章|章节|正文|开篇|序章|草稿)/;
  return writingAction.test(value) && chapterObject.test(value);
}

function isWritableChapter(chapter: Chapter | null | undefined): chapter is Chapter {
  if (!chapter) return false;
  return !["confirmed", "accepted", "published", "committed"].includes(
    String(chapter.status || "draft").toLowerCase(),
  );
}

function studioChapterStatusLabel(status?: string | null) {
  const value = String(status || "draft").toLowerCase();
  if (["confirmed", "accepted", "published", "committed"].includes(value)) {
    return "已完成";
  }
  if (["generating", "running", "queued"].includes(value)) return "写作中";
  if (["failed", "rejected"].includes(value)) return "需处理";
  return "草稿";
}

function conversationWorkMode(purpose?: string): AgentWorkMode {
  return ["global", "global_story", "setup_global"].includes(
    String(purpose || "").toLowerCase(),
  )
    ? "global"
    : "chapter";
}

interface StoryStudioProps {
  project: Project;
  storyMap: StoryMap;
  chapters: Chapter[];
  activeChapter: Chapter | null;
  activeContent: string;
  assistantProvider?: ProviderProfile | null;
  memoryRun: MemoryRun | null;
  projectMemory?: ProjectMemory | null;
  initialMode?: StudioMode;
  autoOpenAgent?: boolean;
  onContentChange: (content: string) => void;
  onCreateChapter: () => void;
  onImport: () => void;
  onGenerate: () => void;
  onModeChange: (mode: StudioMode) => void;
  onBack: () => void;
  onChapter: (chapter: Chapter) => void;
  onAnalyzeMemory: () => void;
  onRetryMemory?: () => void;
  onMemoryRun?: (run: MemoryRun) => void;
  onNotice?: (tone: NoticeTone, message: string) => void;
}

const emptyCharacter = (projectId: string): CharacterCard => ({
  id: "new-character",
  project_id: projectId,
  name: "",
  aliases: [],
  role: "",
  age: "",
  gender: "",
  pronouns: "",
  appearance: "",
  personality: "",
  occupation: "",
  background: "",
  goals: "",
  motivation: "",
  conflict_fears: "",
  abilities: "",
  arc: "",
  voice: "",
  tags: [],
  custom_fields: {},
  portrait: null,
  status: "active",
  source_refs: [],
});

const fieldGroups: Array<{
  title: string;
  fields: Array<{
    key: keyof CharacterCard;
    label: string;
    placeholder: string;
    multiline?: boolean;
  }>;
}> = [
  {
    title: "角色坐标",
    fields: [
      { key: "name", label: "姓名", placeholder: "角色在故事里的称呼" },
      {
        key: "aliases",
        label: "别名",
        placeholder: "用逗号分隔，例如：小林、灯塔守",
      },
      {
        key: "role",
        label: "身份 / 作用",
        placeholder: "主角、引路人、隐藏反派…",
      },
      { key: "age", label: "年龄", placeholder: "可写年龄或人生阶段" },
      { key: "gender", label: "性别", placeholder: "可留空" },
      { key: "pronouns", label: "称谓", placeholder: "他 / 她 / 祂 / 名字" },
      { key: "occupation", label: "职业", placeholder: "他如何在世界里谋生？" },
    ],
  },
  {
    title: "可被看见的部分",
    fields: [
      {
        key: "appearance",
        label: "外貌",
        placeholder: "让读者能记住的细节…",
        multiline: true,
      },
      {
        key: "personality",
        label: "性格",
        placeholder: "习惯、底色、矛盾…",
        multiline: true,
      },
      {
        key: "background",
        label: "背景",
        placeholder: "来自哪里，经历过什么？",
        multiline: true,
      },
      {
        key: "abilities",
        label: "能力 / 局限",
        placeholder: "擅长什么，又付出什么代价？",
        multiline: true,
      },
      {
        key: "voice",
        label: "说话方式",
        placeholder: "节奏、口头禅、避讳…",
        multiline: true,
      },
    ],
  },
  {
    title: "推动故事的部分",
    fields: [
      {
        key: "motivation",
        label: "深层动机",
        placeholder: "他真正想要什么？",
        multiline: true,
      },
      {
        key: "goals",
        label: "目标",
        placeholder: "这一阶段正在追逐什么？",
        multiline: true,
      },
      {
        key: "conflict_fears",
        label: "冲突 / 恐惧",
        placeholder: "阻碍和最害怕失去的是什么？",
        multiline: true,
      },
      {
        key: "arc",
        label: "人物弧",
        placeholder: "他会如何改变？",
        multiline: true,
      },
    ],
  },
];

function characterPayload(character: CharacterCard): Record<string, unknown> {
  return {
    name: character.name.trim() || "未命名人物",
    aliases: character.aliases,
    role: character.role || "",
    age: character.age || "",
    gender: character.gender || "",
    pronouns: character.pronouns || "",
    appearance: character.appearance || "",
    personality: character.personality || "",
    motivation: character.motivation || "",
    goals: character.goals || "",
    conflict_fears: character.conflict_fears || "",
    occupation: character.occupation || "",
    background: character.background || "",
    abilities: character.abilities || "",
    arc: character.arc || "",
    voice: character.voice || "",
    tags: character.tags,
    custom_fields: character.custom_fields,
    // Clicking manual save is an explicit confirmation. Agent proposals stay
    // pending until applied; a saved card is active unless it was already
    // confirmed, which should remain confirmed.
    status: character.status === "confirmed" ? "confirmed" : "active",
    image_media_id: character.image_media_id || null,
    source_type: "manual",
    ...(character.version ? { expected_version: character.version } : {}),
  };
}

function fieldValue(
  character: CharacterCard,
  key: keyof CharacterCard,
): string {
  const value = character[key];
  if (Array.isArray(value)) return value.join("、");
  if (value === undefined || value === null) return "";
  return String(value);
}

function patchPath(path: string) {
  return path.replace(/^(character|人物)\.?/, "").replace(/^profile\.?/, "");
}

function applyCharacterPatch(
  character: CharacterCard,
  patch: AgentPatch,
): CharacterCard {
  const key = patchPath(patch.path) as keyof CharacterCard;
  if (!(key in character)) return character;
  if (key === "aliases" || key === "tags") {
    const value = Array.isArray(patch.value)
      ? patch.value.map(String)
      : String(patch.value ?? "")
          .split(/[、,，\n]/)
          .map((item) => item.trim())
          .filter(Boolean);
    return { ...character, [key]: value } as CharacterCard;
  }
  if (
    typeof character[key] === "object" &&
    key !== "portrait" &&
    key !== "custom_fields"
  ) {
    return character;
  }
  return { ...character, [key]: String(patch.value ?? "") } as CharacterCard;
}

function isCharacterProposal(proposal: AssistantProposal) {
  return (
    proposal.target.type === "character" ||
    proposal.target_type === "character" ||
    String(proposal.operation || "").includes("character")
  );
}

function isGraphProposal(proposal: AssistantProposal) {
  const operation = String(proposal.operation || "").toLowerCase();
  const targetType = String(
    proposal.target_type || proposal.target.type || "",
  ).toLowerCase();
  return (
    targetType.includes("graph") ||
    targetType.includes("thread") ||
    targetType.includes("relation") ||
    targetType.includes("relationship") ||
    operation.includes("graph") ||
    operation.includes("edge") ||
    operation.includes("relation")
  );
}

function targetsCharacter(
  proposal: AssistantProposal,
  character: CharacterCard,
) {
  if (!isCharacterProposal(proposal)) return false;
  const targetId = proposal.target_id || proposal.target.id;
  return (
    !targetId ||
    targetId === "new-character" ||
    character.id === "new-character" ||
    targetId === character.id
  );
}

function proposalWithPatch(
  proposal: AssistantProposal,
  patch: AgentPatch,
): AssistantProposal {
  const normalizedPath = patchPath(patch.path);
  const patches = [
    ...proposal.patches.filter(
      (item) => patchPath(item.path) !== normalizedPath,
    ),
    patch,
  ];
  return { ...proposal, patches };
}

function characterAgentDrafts(
  character: CharacterCard | null,
  proposals: AssistantProposal[],
  manualPaths: Set<string>,
): Record<string, AgentFieldDraft> {
  if (!character) return {};
  const result: Record<string, AgentFieldDraft> = {};
  proposals
    .filter(
      (proposal) =>
        isPreviewProposal(proposal) && targetsCharacter(proposal, character),
    )
    .forEach((proposal) => {
      proposal.patches.forEach((patch) => {
        const path = patchPath(patch.path);
        if (!(path in character)) return;
        result[path] = {
          proposalId: proposal.id,
          label: patch.label || path,
          status: proposal.status,
          before: fieldValue(character, path as keyof CharacterCard),
          value: patch.value,
          conflict: manualPaths.has(path),
        };
      });
    });
  return result;
}

function proposalDraftCharacter(
  projectId: string,
  proposal: AssistantProposal,
): CharacterCard | null {
  if (!isCharacterProposal(proposal) || !isPreviewProposal(proposal))
    return null;
  if (
    proposal.target_id ||
    (proposal.target.id && proposal.target.id !== "new-character")
  )
    return null;
  return proposal.patches.reduce<CharacterCard>(
    (card, patch) => applyCharacterPatch(card, patch),
    {
      ...emptyCharacter(projectId),
      id: `agent-draft-${proposal.id}`,
      status: "pending",
    } as CharacterCard,
  );
}

function chapterPatchValues(proposal: AssistantProposal) {
  return Object.fromEntries(
    proposal.patches.map((patch) => [patchPath(patch.path), patch.value]),
  ) as Record<string, unknown>;
}

export function chapterAgentDraft(
  chapter: Pick<Chapter, "id"> | null,
  content: string,
  proposals: AssistantProposal[],
): ChapterAgentDraft | null {
  if (!chapter) return null;
  for (const proposal of [...proposals].reverse()) {
    if (!isPreviewProposal(proposal)) continue;
    const operation = String(proposal.operation || "").toLowerCase();
    const targetType = String(
      proposal.target_type || proposal.target.type || "",
    ).toLowerCase();
    if (!targetType.includes("chapter") && !operation.includes("chapter")) {
      continue;
    }
    const patch = chapterPatchValues(proposal);
    const patchChapterId = String(
      patch.chapter_id ?? patch.chapterId ?? "",
    );
    const proposalChapterId = String(
      proposal.target_id || proposal.target.id || patchChapterId || "",
    );
    if (
      (proposalChapterId && proposalChapterId !== chapter.id) &&
      patchChapterId !== chapter.id
    ) {
      continue;
    }
    const directPatch = proposal.patches.find((item) =>
      ["new_content", "newContent"].includes(patchPath(item.path)),
    );
    const replacementPatch = proposal.patches.find((item) =>
      ["replacement", "new_text", "newText", "content"].includes(
        patchPath(item.path),
      ),
    );
    const directContent = directPatch?.value;
    const replacementValue = replacementPatch?.value;
    if (typeof directContent !== "string" && typeof replacementValue !== "string") {
      continue;
    }
    const startValue = Number(
      patch.selection_start ?? patch.selectionStart ?? patch.start ?? 0,
    );
    const endValue = Number(
      patch.selection_end ?? patch.selectionEnd ?? patch.end ?? content.length,
    );
    const start = Number.isFinite(startValue)
      ? Math.max(0, Math.min(content.length, startValue))
      : 0;
    const end = Number.isFinite(endValue)
      ? Math.max(start, Math.min(content.length, endValue))
      : content.length;
    const replacement =
      typeof directContent === "string"
        ? directContent
        : String(replacementValue || "");
    const after =
      typeof directContent === "string"
        ? directContent
        : content.slice(0, start) + replacement + content.slice(end);
    return {
      proposalId: proposal.id,
      summary: proposal.summary,
      status: proposal.status,
      before: content,
      after,
      replacement,
      editPath: String((directPatch || replacementPatch)?.path || "replacement"),
      editValue:
        typeof directContent === "string" ? directContent : replacement,
      editsWholeChapter: typeof directContent === "string",
      start,
      end,
    };
  }
  return null;
}

function findGraphNode(
  graph: StoryGraph,
  value: unknown,
): StoryGraphNode | undefined {
  if (!value) return undefined;
  const raw =
    typeof value === "object" && value
      ? String(
          (value as Record<string, unknown>).node_id ||
            (value as Record<string, unknown>).character_id ||
            (value as Record<string, unknown>).id ||
            (value as Record<string, unknown>).name ||
            "",
        )
      : String(value);
  return graph.nodes.find(
    (node) =>
      node.id === raw ||
      node.ref_id === raw ||
      node.character_id === raw ||
      node.label === raw,
  );
}

const GRAPH_NODE_GAP_X = 340;
const GRAPH_NODE_GAP_Y = 150;
const GRAPH_LAYOUT_STEP_X = 390;
const GRAPH_LAYOUT_STEP_Y = 190;

function graphPositionCollides(
  position: { x: number; y: number },
  occupied: Array<{ x: number; y: number }>,
) {
  return occupied.some(
    (item) =>
      Math.abs(item.x - position.x) < GRAPH_NODE_GAP_X &&
      Math.abs(item.y - position.y) < GRAPH_NODE_GAP_Y,
  );
}

function nextOpenGraphPosition(
  occupied: Array<{ x: number; y: number }>,
) {
  for (let index = 0; index < 240; index += 1) {
    const columns = Math.max(2, Math.min(4, Math.ceil(Math.sqrt(occupied.length + 1))));
    const row = Math.floor(index / columns);
    const column = index % columns;
    const candidate = {
      x: 80 + column * GRAPH_LAYOUT_STEP_X,
      y: 80 + row * GRAPH_LAYOUT_STEP_Y,
    };
    if (!graphPositionCollides(candidate, occupied)) return candidate;
  }
  return { x: 80, y: 80 + occupied.length * GRAPH_LAYOUT_STEP_Y };
}

export function spreadOverlappingGraphNodes(
  nodes: StoryGraphNode[],
  edges: StoryGraphEdge[] = [],
): StoryGraphNode[] {
  const originals = nodes.map((node) => ({
    x: Number.isFinite(node.position?.x) ? node.position.x : 0,
    y: Number.isFinite(node.position?.y) ? node.position.y : 0,
  }));
  const hasNodeCollision = originals.some((position, index) =>
    graphPositionCollides(position, originals.slice(0, index)),
  );
  const positionById = new Map(
    nodes.map((node, index) => [node.id, originals[index]]),
  );
  const hasEdgeThroughNode = edges.some((edge) => {
    const source = positionById.get(edge.source);
    const target = positionById.get(edge.target);
    if (!source || !target) return false;
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distanceSquared = dx * dx + dy * dy;
    if (distanceSquared < 1) return false;
    return nodes.some((node) => {
      if (node.id === edge.source || node.id === edge.target) return false;
      const position = positionById.get(node.id);
      if (!position) return false;
      const projection =
        ((position.x - source.x) * dx + (position.y - source.y) * dy) /
        distanceSquared;
      if (projection <= 0.12 || projection >= 0.88) return false;
      const projectedX = source.x + projection * dx;
      const projectedY = source.y + projection * dy;
      return (
        Math.abs(position.x - projectedX) < GRAPH_NODE_GAP_X * 0.58 &&
        Math.abs(position.y - projectedY) < GRAPH_NODE_GAP_Y * 0.72
      );
    });
  });
  const needsLayout = hasNodeCollision || hasEdgeThroughNode;
  if (!needsLayout) return nodes;

  const byId = new Map(nodes.map((node, index) => [node.id, index]));
  const outgoing = new Map<string, string[]>();
  const indegree = new Map(nodes.map((node) => [node.id, 0]));
  edges.forEach((edge) => {
    if (!byId.has(edge.source) || !byId.has(edge.target)) return;
    outgoing.set(edge.source, [...(outgoing.get(edge.source) || []), edge.target]);
    indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1);
  });
  const ranks = new Map<string, number>();
  const queue = nodes
    .filter((node) => (indegree.get(node.id) || 0) === 0)
    .map((node) => node.id);
  queue.forEach((id) => ranks.set(id, 0));
  while (queue.length) {
    const source = queue.shift() as string;
    const sourceRank = ranks.get(source) || 0;
    (outgoing.get(source) || []).forEach((target) => {
      ranks.set(target, Math.max(ranks.get(target) || 0, sourceRank + 1));
      indegree.set(target, (indegree.get(target) || 0) - 1);
      if ((indegree.get(target) || 0) === 0) queue.push(target);
    });
  }
  nodes.forEach((node, index) => {
    if (!ranks.has(node.id)) ranks.set(node.id, edges.length ? index % 3 : 0);
  });
  const layers = new Map<number, StoryGraphNode[]>();
  nodes.forEach((node) => {
    const rank = ranks.get(node.id) || 0;
    layers.set(rank, [...(layers.get(rank) || []), node]);
  });
  const positions = new Map<string, { x: number; y: number }>();
  if (!edges.length) {
    const columns = Math.max(2, Math.min(4, Math.ceil(Math.sqrt(nodes.length))));
    nodes.forEach((node, index) => {
      positions.set(node.id, {
        x: 80 + (index % columns) * GRAPH_LAYOUT_STEP_X,
        y: 80 + Math.floor(index / columns) * GRAPH_LAYOUT_STEP_Y,
      });
    });
  } else {
    [...layers.entries()]
      .sort(([left], [right]) => left - right)
      .forEach(([rank, layer]) => {
        const verticalOffset =
          Math.max(0, (nodes.length - layer.length) * 18) +
          (rank % 2 === 1 ? Math.round(GRAPH_LAYOUT_STEP_Y * 0.78) : 0);
        layer.forEach((node, index) => {
          positions.set(node.id, {
            x: 80 + rank * GRAPH_LAYOUT_STEP_X,
            y: 80 + verticalOffset + index * GRAPH_LAYOUT_STEP_Y,
          });
        });
      });
  }
  const occupied: Array<{ x: number; y: number }> = [];
  return nodes.map((node) => {
    let position = positions.get(node.id) || nextOpenGraphPosition(occupied);
    if (graphPositionCollides(position, occupied)) {
      position = nextOpenGraphPosition(occupied);
    }
    occupied.push(position);
    return { ...node, position };
  });
}

export function graphWithAgentDrafts(
  graph: StoryGraph,
  proposals: AssistantProposal[],
): StoryGraph {
  const nodes = graph.nodes.map((node) => ({
    ...node,
    data: { ...(node.data || {}) },
  }));
  const edges = graph.edges.map((edge) => ({
    ...edge,
    data: { ...(edge.data || {}) },
  }));
  const working: StoryGraph = { ...graph, nodes, edges };
  const ensureDraftEndpoint = (
    value: unknown,
    proposal: AssistantProposal,
    endpointIndex: number,
  ) => {
    const existing = findGraphNode(working, value);
    if (existing) return existing;
    const label =
      typeof value === "object" && value
        ? String(
            (value as Record<string, unknown>).name ||
              (value as Record<string, unknown>).label ||
              (value as Record<string, unknown>).id ||
              "",
          ).trim()
        : String(value || "").trim();
    if (!label) return undefined;
    const endpoint: StoryGraphNode = {
      id: `agent-draft-endpoint-${textHash(label)}`,
      type: "character",
      label,
      subtitle: "关系草稿端点",
      status: proposal.status === "building" ? "Agent 制作中" : "Agent 草稿",
      position: {
        x: 90 + (endpointIndex % 3) * GRAPH_LAYOUT_STEP_X,
        y: 90 + Math.floor(endpointIndex / 3) * GRAPH_LAYOUT_STEP_Y,
      },
      data: {
        agentDraft: true,
        agentEndpointDraft: true,
        sourceProposalId: proposal.id,
        draftStatus: proposal.status,
      },
    };
    working.nodes.push(endpoint);
    return endpoint;
  };
  const previewRows = proposals.filter(isPreviewProposal);
  // Provider/list responses are commonly newest-first, which puts a
  // relationship before the two character proposals it references. Build
  // every node first, then resolve edge names against that complete draft
  // graph so the canvas and the accessible relation table stay equivalent.
  const orderedRows = [
    ...previewRows.filter(
      (proposal) =>
        !String(proposal.operation || "").toLowerCase().includes("graph_edge"),
    ),
    ...previewRows.filter((proposal) =>
      String(proposal.operation || "").toLowerCase().includes("graph_edge"),
    ),
  ];
  orderedRows
    .forEach((proposal, index) => {
      const operation = String(proposal.operation || "");
      const patch = Object.fromEntries(
        proposal.patches.map((item) => [patchPath(item.path), item.value]),
      );
      if (isCharacterProposal(proposal)) {
        const targetId = proposal.target_id || proposal.target.id;
        const existing = targetId
          ? working.nodes.find(
              (node) =>
                node.character_id === targetId || node.ref_id === targetId,
            )
          : undefined;
        if (existing) {
          existing.label = String(patch.name ?? existing.label);
          existing.subtitle = String(patch.role ?? existing.subtitle ?? "");
          existing.data = {
            ...(existing.data || {}),
            agentDraft: true,
            proposalId: proposal.id,
            draftPatches: proposalUpdatePatches(proposal),
            draftStatus: proposal.status,
          };
          existing.status =
            proposal.status === "building" ? "Agent 制作中" : "Agent 草稿";
        } else {
          working.nodes.push({
            id: `agent-draft-node-${proposal.id}`,
            type: "character",
            label: String(patch.name || "Agent 建议人物"),
            subtitle: String(patch.role || patch.motivation || "资料生成中"),
            status:
              proposal.status === "building" ? "Agent 制作中" : "Agent 草稿",
            position: {
              x: 90 + (index % 3) * GRAPH_LAYOUT_STEP_X,
              y: 90 + Math.floor(index / 3) * GRAPH_LAYOUT_STEP_Y,
            },
            data: {
              agentDraft: true,
              proposalId: proposal.id,
              draftPatches: proposalUpdatePatches(proposal),
              draftStatus: proposal.status,
            },
          });
        }
        return;
      }
      if (operation.includes("graph_node")) {
        const targetId = proposal.target_id || proposal.target.id;
        const existing = targetId
          ? working.nodes.find((node) => node.id === targetId)
          : undefined;
        if (existing) {
          existing.label = String(patch.label ?? existing.label);
          existing.subtitle = String(patch.subtitle ?? existing.subtitle ?? "");
          existing.data = {
            ...(existing.data || {}),
            ...patch,
            agentDraft: true,
            proposalId: proposal.id,
            draftPatches: proposalUpdatePatches(proposal),
            draftStatus: proposal.status,
          };
          existing.status =
            proposal.status === "building" ? "Agent 制作中" : "Agent 草稿";
        } else {
          const rawType = String(patch.node_type || patch.type || "event");
          const type: StoryGraphNode["type"] =
            rawType === "character" || rawType === "thread" ? rawType : "event";
          working.nodes.push({
            id: `agent-draft-node-${proposal.id}`,
            type,
            label: String(patch.label || patch.name || "Agent 建议节点"),
            subtitle: String(patch.subtitle || "资料生成中"),
            status:
              proposal.status === "building" ? "Agent 制作中" : "Agent 草稿",
            position: {
              x: 90 + (index % 3) * GRAPH_LAYOUT_STEP_X,
              y: 90 + Math.floor(index / 3) * GRAPH_LAYOUT_STEP_Y,
            },
            data: {
              ...patch,
              agentDraft: true,
              proposalId: proposal.id,
              draftPatches: proposalUpdatePatches(proposal),
              draftStatus: proposal.status,
            },
          });
        }
        return;
      }
      if (operation.includes("graph_edge")) {
        const sourceValue =
          patch.source_node_id ||
          patch.source_character_id ||
          patch.source_character ||
          patch.source_name ||
          patch.source ||
          patch.from;
        const targetValue =
          patch.target_node_id ||
          patch.target_character_id ||
          patch.target_character ||
          patch.target_name ||
          patch.target ||
          patch.to;
        // Relationship patches can arrive before their endpoint nodes. Keep
        // temporary endpoints in the live canvas until the complete batch is
        // persisted, so the user can watch the graph form without flicker.
        const source = ensureDraftEndpoint(
          sourceValue,
          proposal,
          working.nodes.length,
        );
        const target = ensureDraftEndpoint(
          targetValue,
          proposal,
          working.nodes.length,
        );
        if (!source || !target || source.id === target.id) return;
        working.edges.push({
          id: `agent-draft-edge-${proposal.id}`,
          source: source.id,
          target: target.id,
          label: String(
            patch.label || patch.relation_type || patch.relation || "建议关系",
          ),
          kind: String(patch.relation_type || patch.relation || "relationship"),
          direction: patch.directed === false ? "undirected" : "directed",
          status: "draft",
          data: {
            ...patch,
            agentDraft: true,
            proposalId: proposal.id,
            draftPatches: proposalUpdatePatches(proposal),
            draftStatus: proposal.status,
          },
        });
      }
    });
  return {
    ...working,
    nodes: spreadOverlappingGraphNodes(working.nodes, working.edges),
  };
}

export type AgentBuildSummary = {
  total: number;
  readyCount: number;
  building: boolean;
  chapterCount: number;
  characterCount: number;
  graphProposalCount: number;
  nodeCount: number;
  edgeCount: number;
  patchCount: number;
};

export function summarizeAgentBuild(
  proposals: AssistantProposal[],
  graph: StoryGraph,
): AgentBuildSummary {
  const previewProposals = proposals.filter(isPreviewProposal);
  const structureProposals = previewProposals.filter(
    (proposal) => isCharacterProposal(proposal) || isGraphProposal(proposal),
  );
  return {
    total: previewProposals.length,
    readyCount: previewProposals.filter(
      (proposal) => proposal.status === "proposed",
    ).length,
    building: previewProposals.some(
      (proposal) => proposal.status === "building",
    ),
    chapterCount: previewProposals.filter((proposal) => {
      const operation = String(proposal.operation || "").toLowerCase();
      const type = String(
        proposal.target_type || proposal.target.type || "",
      ).toLowerCase();
      return operation.includes("chapter") || type.includes("chapter");
    }).length,
    characterCount: structureProposals.filter(
      (proposal) => isCharacterProposal(proposal) && !isGraphProposal(proposal),
    ).length,
    graphProposalCount: structureProposals.filter(isGraphProposal).length,
    nodeCount: graph.nodes.filter((node) => node.data?.agentDraft).length,
    edgeCount: graph.edges.filter((edge) => edge.data?.agentDraft).length,
    patchCount: previewProposals.reduce(
      (total, proposal) => total + proposal.patches.length,
      0,
    ),
  };
}

function notifyFallback(
  onNotice: StoryStudioProps["onNotice"],
  tone: NoticeTone,
  message: string,
) {
  onNotice?.(tone, message);
}

function formatDate(value?: string) {
  if (!value) return "刚刚";
  return value.replace("T", " ").slice(0, 16);
}

function CharacterPortrait({
  character,
  large = false,
}: {
  character: CharacterCard;
  large?: boolean;
}) {
  if (character.portrait?.url) {
    return (
      <img
        className={large ? "character-portrait large" : "character-portrait"}
        src={character.portrait.url}
        alt={character.portrait.alt || `${character.name || "人物"}头像`}
      />
    );
  }
  return (
    <span
      className={
        large
          ? "character-portrait character-portrait-placeholder large"
          : "character-portrait character-portrait-placeholder"
      }
      aria-hidden="true"
    >
      {(character.name || "人").slice(0, 1)}
    </span>
  );
}

function CharacterCardView({
  character,
  onOpen,
}: {
  character: CharacterCard;
  onOpen: () => void;
}) {
  return (
    <motion.article
      layoutId={`character-card-${character.id}`}
      className="character-card"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      aria-label={`打开人物${character.name || "未命名人物"}`}
    >
      <div className="character-card-image">
        <CharacterPortrait character={character} />
        <span className={`character-card-status status-${character.status}`}>
          {character.status === "confirmed"
            ? "已入典"
            : character.status === "active"
              ? "已生效"
              : character.status === "needs_review"
                ? "需整理"
                : "草稿"}
        </span>
      </div>
      <div className="character-card-copy">
        <span className="eyebrow">人物资料</span>
        <h3>{character.name || "未命名人物"}</h3>
        <p>
          {character.role ||
            character.motivation ||
            character.goals ||
            "还没有角色摘要。"}
        </p>
        <div className="character-card-tags">
          {(character.tags.length ? character.tags : ["待补全"])
            .slice(0, 3)
            .map((tag) => (
              <span key={tag}>{tag}</span>
            ))}
        </div>
      </div>
      <span className="character-card-open">
        <ArrowRight size={15} />
      </span>
    </motion.article>
  );
}

function CharacterDetailOverlay({
  character,
  onClose,
}: {
  character: CharacterCard;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const layerRef = useRef<HTMLDivElement>(null);
  const reduceMotion = useReducedMotion();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusables = layerRef.current?.querySelectorAll<HTMLElement>(
        "button, [href], input, textarea, select, [tabindex]:not([tabindex='-1'])",
      );
      if (!focusables?.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <motion.div
      ref={layerRef}
      className="character-detail-layer"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="character-detail-title"
    >
      <button
        className="character-detail-scrim"
        onClick={onClose}
        aria-label="关闭人物详情"
      />
      <motion.article
        layoutId={`character-card-${character.id}`}
        className="character-detail-card"
        transition={
          reduceMotion
            ? { duration: 0 }
            : { type: "spring", stiffness: 280, damping: 28 }
        }
      >
        <div className="character-detail-hero">
          <CharacterPortrait character={character} large />
          <div>
            <span className="eyebrow">
              人物资料 · {character.status === "confirmed" ? "已生效" : character.status === "needs_review" ? "需整理" : "草稿"}
            </span>
            <h2 id="character-detail-title">
              {character.name || "未命名人物"}
            </h2>
            <p>{character.role || "尚未填写身份"}</p>
          </div>
          <button
            ref={closeRef}
            className="quiet-icon"
            onClick={onClose}
            aria-label="关闭人物详情"
          >
            <X size={17} />
          </button>
        </div>
        <div className="character-detail-body">
          <div className="character-detail-stats">
            <span>
              <small>别名</small>
              <strong>{character.aliases.join(" / ") || "—"}</strong>
            </span>
            <span>
              <small>年龄</small>
              <strong>{character.age || "—"}</strong>
            </span>
            <span>
              <small>称谓</small>
              <strong>{character.pronouns || "—"}</strong>
            </span>
          </div>
          <div className="character-detail-grid">
            {fieldGroups
              .flatMap((group) => group.fields)
              .filter(
                (field) =>
                  field.key !== "name" &&
                  field.key !== "aliases" &&
                  fieldValue(character, field.key),
              )
              .map((field) => (
                <section key={String(field.key)}>
                  <span>{field.label}</span>
                  <p>{fieldValue(character, field.key)}</p>
                </section>
              ))}
          </div>
          <div className="character-detail-footer">
            <span>
              <CheckCircle2 size={13} /> 设定与正文保持项目隔离
            </span>
            <span>更新于 {formatDate(character.updated_at)}</span>
          </div>
        </div>
      </motion.article>
    </motion.div>
  );
}

function CharacterImagePicker({
  character,
  onFile,
  onRemove,
  busy,
}: {
  character: CharacterCard;
  onFile: (file: File) => void;
  onRemove: () => void;
  busy: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div className="character-image-picker">
      <div className="character-image-preview">
        <CharacterPortrait character={character} large />
      </div>
      <div>
        <strong>人物肖像</strong>
        <p>支持 JPG、PNG、WebP，单张不超过 10 MB。</p>
        <div className="character-image-actions">
          <button
            type="button"
            className="button button-secondary button-small"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
          >
            <Upload size={13} /> {character.portrait ? "更换图片" : "上传图片"}
          </button>
          {character.portrait && (
            <button
              type="button"
              className="text-button text-danger"
              onClick={onRemove}
              disabled={busy}
            >
              <Trash2 size={13} /> 移除
            </button>
          )}
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="visually-hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFile(file);
          event.target.value = "";
        }}
      />
    </div>
  );
}

function CharacterForm({
  character,
  onChange,
  onSave,
  onUpload,
  onRemovePortrait,
  busy,
  agentDrafts,
}: {
  character: CharacterCard;
  onChange: (next: CharacterCard, path?: string) => void;
  onSave: () => void;
  onUpload: (file: File) => void;
  onRemovePortrait: () => void;
  busy: boolean;
  agentDrafts: Record<string, AgentFieldDraft>;
}) {
  const update = (key: keyof CharacterCard, value: string) => {
    if (key === "aliases" || key === "tags") {
      onChange(
        {
          ...character,
          [key]: value
            .split(/[、,，\n]/)
            .map((item) => item.trim())
            .filter(Boolean),
        } as CharacterCard,
        String(key),
      );
    } else
      onChange({ ...character, [key]: value } as CharacterCard, String(key));
  };
  const [customKey, setCustomKey] = useState("");
  const [customValue, setCustomValue] = useState("");
  const addCustom = () => {
    const key = customKey.trim();
    if (!key) return;
    onChange(
      {
        ...character,
        custom_fields: { ...character.custom_fields, [key]: customValue },
      },
      `custom_fields.${key}`,
    );
    setCustomKey("");
    setCustomValue("");
  };
  return (
    <div className="character-form-shell">
      <CharacterImagePicker
        character={character}
        onFile={onUpload}
        onRemove={onRemovePortrait}
        busy={busy}
      />
      {fieldGroups.map((group) => (
        <section className="character-form-group" key={group.title}>
          <div className="character-form-group-head">
            <span className="eyebrow">{group.title}</span>
            <small>手动填写，或在右侧让 Agent 提建议</small>
          </div>
          <div className="character-form-table">
            {group.fields.map((field) => {
              const path = String(field.key);
              const draft = agentDrafts[path];
              const draftValue = draft
                ? Array.isArray(draft.value)
                  ? draft.value.join("、")
                  : String(draft.value ?? "")
                : "";
              return (
                <label
                  className={`character-field ${draft ? "is-agent-updated" : ""} ${draft?.conflict ? "has-agent-conflict" : ""}`}
                  key={path}
                >
                  <span>{field.label}</span>
                  {field.multiline ? (
                    <textarea
                      rows={3}
                      value={fieldValue(character, field.key)}
                      onChange={(event) =>
                        update(field.key, event.target.value)
                      }
                      placeholder={field.placeholder}
                    />
                  ) : (
                    <input
                      value={fieldValue(character, field.key)}
                      onChange={(event) =>
                        update(field.key, event.target.value)
                      }
                      placeholder={field.placeholder}
                    />
                  )}
                  {draft && (
                    <div className="agent-field-draft">
                      <em>
                        <Sparkles size={11} /> Agent 草稿
                        {draft.conflict ? " · 与手动编辑冲突" : ""}
                      </em>
                      <small>
                        <del>{draft.before || "空白"}</del>
                        <ArrowRight size={11} />
                        <strong>{draftValue || "空白"}</strong>
                      </small>
                      <span className="agent-draft-batch-hint">
                        {draft.status === "building" ? "Agent 正在补全" : "即将自动保存"}
                      </span>
                    </div>
                  )}
                </label>
              );
            })}
          </div>
        </section>
      ))}
      <section className="character-form-group">
        <div className="character-form-group-head">
          <span className="eyebrow">标签与自定义字段</span>
          <small>给这张卷宗留下你的索引</small>
        </div>
        <label className="character-field">
          <span>标签</span>
          <input
            value={character.tags.join("、")}
            onChange={(event) => update("tags", event.target.value)}
            placeholder="主角、灯塔、秘密"
          />
        </label>
        <div className="custom-field-add">
          <input
            value={customKey}
            onChange={(event) => setCustomKey(event.target.value)}
            placeholder="字段名，例如：秘密"
          />
          <input
            value={customValue}
            onChange={(event) => setCustomValue(event.target.value)}
            placeholder="字段内容"
          />
          <button
            type="button"
            className="button button-secondary button-small"
            onClick={addCustom}
          >
            <Plus size={13} /> 添加字段
          </button>
        </div>
        {Object.entries(character.custom_fields).map(([key, value]) => (
          <div className="custom-field-row" key={key}>
            <strong>{key}</strong>
            <input
              value={value}
              onChange={(event) =>
                onChange(
                  {
                    ...character,
                    custom_fields: {
                      ...character.custom_fields,
                      [key]: event.target.value,
                    },
                  },
                  `custom_fields.${key}`,
                )
              }
            />
            <button
              type="button"
              className="quiet-icon"
              onClick={() => {
                const next = { ...character.custom_fields };
                delete next[key];
                onChange(
                  { ...character, custom_fields: next },
                  `custom_fields.${key}`,
                );
              }}
              aria-label={`删除${key}`}
            >
              <X size={13} />
            </button>
          </div>
        ))}
      </section>
      <div className="character-form-footer">
        <span>
          <CheckCircle2 size={13} /> Agent 改动会自动写入并保存
        </span>
        <button
          type="button"
          className="button button-primary"
          onClick={onSave}
          disabled={busy}
        >
          {busy ? <Loader2 size={14} className="spin" /> : <Check size={14} />}{" "}
          {busy ? "保存中…" : "保存人物卷宗"}
        </button>
      </div>
    </div>
  );
}

type AgentCharacterDraft = {
  proposal: AssistantProposal;
  character: CharacterCard;
};

function AgentCharacterDraftCard({
  proposal,
  character,
}: AgentCharacterDraft) {
  const patches = proposal.patches.slice(0, 6);
  const building = proposal.status === "building";
  return (
    <article
      className={`character-card character-card-agent-draft${building ? " is-agent-building" : ""}`}
      aria-label={`${character.name || "未命名人物"} Agent 草稿`}
    >
      <div className="character-card-image">
        <CharacterPortrait character={character} />
        <span className="character-card-status status-pending">
          {building ? "制卡中" : "Agent 草稿"}
        </span>
      </div>
      <div className="character-card-copy">
        <h3>{character.name || "未命名人物"}</h3>
        <p>
          {character.role || character.motivation || "正在形成的人物建议"}
        </p>
        {patches.length ? (
          <dl className="character-draft-fields">
            {patches.map((patch) => {
              const path = patch.path;
              const value = Array.isArray(patch.value)
                  ? patch.value.join("、")
                  : String(patch.value ?? "");
              return (
                <div key={path}>
                  <dt>{patch.label || patchPath(path)}</dt>
                  <dd>{value || "空白"}</dd>
                </div>
              );
            })}
          </dl>
        ) : (
          <div
            className="character-draft-writing"
            aria-label="Agent 正在填写人物字段"
          >
            <span />
            <span />
            <span />
          </div>
        )}
        <span className="character-draft-progress" role="status">
          {building
            ? patches.length
              ? `正在写入第 ${patches.length + 1} 项资料…`
              : "正在起草第一项资料…"
            : `${proposal.patches.length} 项资料正在自动保存`}
        </span>
        <small>
          {building
            ? "字段到达时会逐项填入这张卡片"
            : "资料已生成，正在自动写入人物卷宗"}
        </small>
      </div>
    </article>
  );
}

function CharacterGallery({
  characters,
  draftCharacters = [],
  onOpen,
  onCreate,
}: {
  characters: CharacterCard[];
  draftCharacters?: AgentCharacterDraft[];
  onOpen: (character: CharacterCard) => void;
  onCreate: () => void;
}) {
  return (
    <section
      className="character-gallery"
      aria-labelledby="character-gallery-title"
    >
      <div className="character-gallery-head">
        <div>
          <span className="eyebrow">人物卡片</span>
          <h3 id="character-gallery-title">人物卡片</h3>
          <p>点击卡片展卷查看；选择表格继续编辑。</p>
        </div>
        <button
          type="button"
          className="button button-secondary button-small"
          onClick={onCreate}
        >
          <Plus size={13} /> 新增卡片
        </button>
      </div>
      {characters.length || draftCharacters.length ? (
        <div className="character-gallery-grid">
          {characters.map((character) => (
            <CharacterCardView
              character={character}
              key={character.id}
              onOpen={() => onOpen(character)}
            />
          ))}
          {draftCharacters.map(({ proposal, character }) => (
            <AgentCharacterDraftCard
              key={proposal.id}
              proposal={proposal}
              character={character}
            />
          ))}
        </div>
      ) : (
        <div className="character-gallery-empty">
          <UserRound size={17} />
          <span>还没有人物卡片</span>
          <small>先写下一个名字，或让 Agent 从故事里找出第一位人物。</small>
        </div>
      )}
    </section>
  );
}

type GraphData = {
  label: string;
  subtitle?: string;
  type: StoryGraphNode["type"];
  image_url?: string;
  status?: string;
  ref_id?: string | null;
  character_id?: string | null;
  chapter_id?: string | null;
  plot_thread_id?: string | null;
  source_refs?: SourceRef[];
  version?: number;
  agentDraft?: boolean;
  proposalId?: string;
  draftPatches?: AgentDraftPatch[];
  draftStatus?: AssistantProposalStatus;
  agentEndpointDraft?: boolean;
  [key: string]: unknown;
};
type FlowNode = Node<GraphData>;
type FlowEdgeData = {
  kind?: string;
  relation_type?: string;
  fullLabel?: string;
  status?: StoryGraphEdge["status"];
  directed?: boolean;
  weight?: number;
  source_refs?: SourceRef[];
  version?: number;
  agentDraft?: boolean;
  proposalId?: string;
  draftPatches?: AgentDraftPatch[];
  draftStatus?: AssistantProposalStatus;
};
type FlowEdge = Edge<FlowEdgeData>;

function fullFlowEdgeLabel(edge: Pick<FlowEdge, "label" | "data">) {
  if (typeof edge.data?.fullLabel === "string") return edge.data.fullLabel;
  return typeof edge.label === "string" ? edge.label : "";
}

function compactGraphEdgeLabel(value: unknown) {
  const label = String(value || "").trim();
  return label.length > 14 ? `${label.slice(0, 13)}…` : label;
}

function GraphNode({ data }: NodeProps<FlowNode>) {
  return (
    <div
      className={`story-flow-node node-${data.type} ${data.agentDraft ? "is-agent-draft" : ""} ${data.draftStatus === "building" ? "is-agent-building" : ""} ${data.agentEndpointDraft ? "is-agent-endpoint-draft" : ""}`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="story-flow-node-icon">
        {data.image_url ? (
          <img src={data.image_url} alt="" />
        ) : data.type === "character" ? (
          <UserRound size={14} />
        ) : data.type === "thread" ? (
          <Network size={14} />
        ) : (
          <PencilLine size={14} />
        )}
      </div>
      <div>
        <strong>{data.label}</strong>
        {data.subtitle && <small>{data.subtitle}</small>}
      </div>
      {data.status && <em>{data.status}</em>}
      {data.agentDraft && data.proposalId && (
        <small className="agent-draft-batch-hint">
          {data.draftStatus === "building" ? "正在生成" : "正在自动保存"}
        </small>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const graphNodeTypes = {
  character: GraphNode,
  thread: GraphNode,
  event: GraphNode,
};

function fallbackGraph(
  project: Project,
  characters: CharacterCard[],
  storyMap: StoryMap,
  activeChapter: Chapter | null,
): StoryGraph {
  const nodes: StoryGraphNode[] = characters.map((character, index) => ({
    id: `character-${character.id}`,
    type: "character",
    label: character.name || "未命名人物",
    subtitle: character.role,
    image_url: character.portrait?.url,
    status: character.status,
    position: {
      x: 80 + (index % 3) * 250,
      y: 80 + Math.floor(index / 3) * 150,
    },
    ref_id: character.id,
    character_id: character.id,
    source_refs: character.source_refs,
    data: {
      is_fallback: true,
      ref_id: character.id,
      character_id: character.id,
      source_refs: character.source_refs,
    },
    scope_chapter_id: activeChapter?.id,
  }));
  (storyMap.threads || [])
    .filter(
      (thread) =>
        !activeChapter ||
        thread.points?.some(
          (point) => point.chapter_number === activeChapter.number,
        ),
    )
    .slice(0, 6)
    .forEach((thread, index) =>
    nodes.push({
      id: `thread-${thread.id}`,
      type: "thread",
      label: thread.title,
      subtitle: thread.next_beat,
      status: thread.status,
      position: {
        x: 100 + (index % 2) * 300,
        y: 440 + Math.floor(index / 2) * 140,
      },
      ref_id: thread.id,
      plot_thread_id: thread.id,
      data: {
        is_fallback: true,
        ref_id: thread.id,
        thread_id: thread.id,
        plot_thread_id: thread.id,
      },
      scope_chapter_id: activeChapter?.id,
    }),
  );
  (storyMap.timeline || [])
    .filter(
      (event) => !activeChapter || event.chapter_id === activeChapter.id,
    )
    .slice(0, 8)
    .forEach((event, index) =>
    nodes.push({
      id: `event-${event.id}`,
      type: "event",
      label: event.title,
      subtitle: event.date_label || event.description,
      status: event.status,
      position: {
        x: 680 + (index % 2) * 260,
        y: 80 + Math.floor(index / 2) * 150,
      },
      ref_id: event.id,
      chapter_id: event.chapter_id,
      source_refs: event.source_ref ? [event.source_ref] : [],
      data: {
        is_fallback: true,
        ref_id: event.id,
        chapter_id: event.chapter_id,
        source_refs: event.source_ref ? [event.source_ref] : [],
        event_id: event.id,
      },
      scope_chapter_id: activeChapter?.id,
    }),
  );
  return { chapter_id: activeChapter?.id, nodes, edges: [] };
}

function graphNodeIdentity(node: StoryGraphNode) {
  const ref =
    node.character_id || node.plot_thread_id || node.chapter_id || node.ref_id;
  return `${node.type}:${ref || node.id}`;
}

function graphNodeTarget(
  node: FlowNode,
  chapterId: string,
): AgentTarget {
  const data = node.data;
  if (data.type === "character") {
    return {
      type: "character",
      id: data.character_id || data.ref_id || node.id,
      chapter_id: chapterId,
    };
  }
  if (data.type === "thread") {
    return {
      type: "thread",
      id: data.plot_thread_id || data.ref_id || node.id,
      chapter_id: chapterId,
    };
  }
  if (data.chapter_id) {
    return { type: "chapter", id: data.chapter_id };
  }
  return { type: "chapter", id: chapterId, chapter_id: chapterId };
}

function mergeStoryGraphs(
  persisted: StoryGraph,
  fallback: StoryGraph,
): StoryGraph {
  const nodes = [...persisted.nodes];
  const identities = new Set(nodes.map(graphNodeIdentity));
  fallback.nodes.forEach((node) => {
    const identity = graphNodeIdentity(node);
    if (identities.has(identity)) return;
    identities.add(identity);
    nodes.push(node);
  });
  const nodeIds = new Set(nodes.map((node) => node.id));
  return {
    ...persisted,
    nodes,
    edges: persisted.edges.filter(
      (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target),
    ),
  };
}

function GraphRelationTable({
  edges,
  nodes,
  onPatch,
  onDelete,
  onAdd,
}: {
  edges: FlowEdge[];
  nodes: FlowNode[];
  onPatch: (id: string, patch: Partial<FlowEdge>) => void;
  onDelete: (id: string) => void;
  onAdd: (
    source: string,
    target: string,
    kind: string,
    label: string,
    directed: boolean,
    status: StoryGraphEdge["status"],
  ) => void;
}) {
  const [source, setSource] = useState(nodes[0]?.id || "");
  const [target, setTarget] = useState(nodes[1]?.id || nodes[0]?.id || "");
  const [kind, setKind] = useState("related");
  const [label, setLabel] = useState("");
  const [directed, setDirected] = useState(true);
  const [status, setStatus] = useState<StoryGraphEdge["status"]>("active");
  const nodeLabel = (id: string) =>
    nodes.find((node) => node.id === id)?.data.label || "未命名节点";
  return (
    <section
      className="relation-table-shell"
      aria-labelledby="relation-table-title"
    >
      <div className="relation-table-head">
        <div>
          <span className="eyebrow">关系索引</span>
          <h3 id="relation-table-title">关系表</h3>
          <p>表格与关系图共享同一份数据，键盘也能完整编辑。</p>
        </div>
        <span className="relation-table-count">{edges.length} 条连线</span>
      </div>
      <div className="relation-table-wrap">
        <table className="relation-table">
          <caption className="visually-hidden">人物和情节关系</caption>
          <thead>
            <tr>
              <th scope="col">来源</th>
              <th scope="col">目标</th>
              <th scope="col">类型</th>
              <th scope="col">标签</th>
              <th scope="col">方向</th>
              <th scope="col">状态</th>
              <th scope="col">
                <span className="visually-hidden">操作</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {edges.map((edge) => (
              <tr key={edge.id}>
                <td>
                  <select
                    aria-label="关系来源"
                    value={edge.source}
                    onChange={(event) =>
                      onPatch(edge.id, { source: event.target.value })
                    }
                  >
                    {nodes.map((node) => (
                      <option value={node.id} key={node.id}>
                        {nodeLabel(node.id)}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    aria-label="关系目标"
                    value={edge.target}
                    onChange={(event) =>
                      onPatch(edge.id, { target: event.target.value })
                    }
                  >
                    {nodes.map((node) => (
                      <option value={node.id} key={node.id}>
                        {nodeLabel(node.id)}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <input
                    aria-label="关系类型"
                    value={String(edge.data?.kind || "related")}
                    onChange={(event) =>
                      onPatch(edge.id, {
                        data: { ...edge.data, kind: event.target.value },
                      })
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label="关系标签"
                    value={fullFlowEdgeLabel(edge)}
                    onChange={(event) =>
                      onPatch(edge.id, { label: event.target.value })
                    }
                    placeholder="可选"
                  />
                </td>
                <td>
                  <select
                    aria-label="关系方向"
                    value={edge.markerEnd ? "directed" : "undirected"}
                    onChange={(event) =>
                      onPatch(edge.id, {
                        markerEnd:
                          event.target.value === "directed"
                            ? { type: MarkerType.ArrowClosed }
                            : undefined,
                      })
                    }
                  >
                    <option value="directed">有向</option>
                    <option value="undirected">无向</option>
                  </select>
                </td>
                <td>
                  <select
                    aria-label="关系状态"
                    value={String(edge.data?.status || "pending")}
                    onChange={(event) =>
                      onPatch(edge.id, {
                        data: {
                          ...edge.data,
                          status: event.target
                            .value as StoryGraphEdge["status"],
                        },
                      })
                    }
                  >
                    <option value="pending">草稿</option>
                    <option value="active">已生效</option>
                    <option value="needs_review">需整理</option>
                    <option value="confirmed">已确认</option>
                    <option value="draft">草稿</option>
                  </select>
                </td>
                <td>
                  <button
                    className="quiet-icon"
                    type="button"
                    onClick={() => onDelete(edge.id)}
                    aria-label={`删除${nodeLabel(edge.source)}与${nodeLabel(edge.target)}的关系`}
                  >
                    <Trash2 size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {edges.some((edge) => edge.data?.agentDraft) && (
          <div className="story-graph-agent-drafts" aria-label="Agent 图谱草稿">
            {edges
              .filter((edge) => edge.data?.agentDraft)
              .map((edge) => (
                <AgentGraphEdgeDraft key={edge.id} edge={edge} nodes={nodes} />
              ))}
          </div>
        )}
        {edges.length === 0 && (
          <div className="relation-table-empty">
            <Link2 size={17} /> 还没有关系；从下方添加第一条。
          </div>
        )}
      </div>
      <form
        className="relation-add-row"
        onSubmit={(event) => {
          event.preventDefault();
          if (!source || !target || source === target) return;
          onAdd(
            source,
            target,
            kind.trim() || "related",
            label.trim(),
            directed,
            status,
          );
          setLabel("");
        }}
      >
        <strong>新增关系</strong>
        <select
          aria-label="新关系来源"
          value={source}
          onChange={(event) => setSource(event.target.value)}
        >
          {nodes.map((node) => (
            <option value={node.id} key={node.id}>
              {nodeLabel(node.id)}
            </option>
          ))}
        </select>
        <ArrowRight size={14} aria-hidden="true" />
        <select
          aria-label="新关系目标"
          value={target}
          onChange={(event) => setTarget(event.target.value)}
        >
          {nodes.map((node) => (
            <option value={node.id} key={node.id}>
              {nodeLabel(node.id)}
            </option>
          ))}
        </select>
        <input
          aria-label="新关系类型"
          value={kind}
          onChange={(event) => setKind(event.target.value)}
          placeholder="类型"
        />
        <input
          aria-label="新关系标签"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="标签"
        />
        <select
          aria-label="新关系方向"
          value={directed ? "directed" : "undirected"}
          onChange={(event) => setDirected(event.target.value === "directed")}
        >
          <option value="directed">有向</option>
          <option value="undirected">无向</option>
        </select>
        <select
          aria-label="新关系状态"
          value={status}
          onChange={(event) =>
            setStatus(event.target.value as StoryGraphEdge["status"])
          }
        >
          <option value="pending">草稿</option>
          <option value="active">已生效</option>
          <option value="needs_review">需整理</option>
        </select>
        <button
          className="button button-secondary button-small"
          type="submit"
          disabled={nodes.length < 2 || source === target}
        >
          <Plus size={13} /> 添加
        </button>
      </form>
    </section>
  );
}

function AgentGraphEdgeDraft({
  edge,
  nodes,
}: {
  edge: FlowEdge;
  nodes: FlowNode[];
}) {
  const source =
    nodes.find((node) => node.id === edge.source)?.data.label || "起点";
  const target =
    nodes.find((node) => node.id === edge.target)?.data.label || "终点";
  return (
    <article className="story-graph-agent-draft">
      <div>
        <span className="eyebrow"><Sparkles size={11} /> Agent 图谱草稿</span>
        <strong>
          {source} <ArrowRight size={12} aria-hidden="true" /> {target}
        </strong>
        <small>
          {edge.data?.draftStatus === "building"
            ? "正在连接人物与情节…"
            : `${fullFlowEdgeLabel(edge) || "建议关系"} · 虚线预览`}
        </small>
      </div>
      {edge.data?.proposalId && (
        <span className="agent-draft-batch-hint">
          {edge.data.draftStatus === "building" ? "连线生成中" : "连线正在自动保存"}
        </span>
      )}
    </article>
  );
}

function StoryGraphView({
  projectId,
  chapterId,
  chapterTitle,
  graph,
  onNotice,
  onTargetChange,
}: {
  projectId: string;
  chapterId: string;
  chapterTitle: string;
  graph: StoryGraph;
  onNotice?: StoryStudioProps["onNotice"];
  onTargetChange?: (target: AgentTarget) => void;
}) {
  const queryClient = useQueryClient();
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge>([]);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [edgeLabel, setEdgeLabel] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saveTick, setSaveTick] = useState(0);
  const [saveErrorAtVersion, setSaveErrorAtVersion] = useState(-1);
  const graphChangeVersionRef = useRef(0);
  const [graphViewMode, setGraphViewMode] = useState<EntityViewMode>(() =>
    typeof window !== "undefined" && window.innerWidth < 760
      ? "table"
      : "graph",
  );
  const [deletedEdgeIds, setDeletedEdgeIds] = useState<string[]>([]);
  // StoryStudio merges durable nodes with derived fallback nodes before this
  // view renders. Keep the canvas bound to that merged snapshot so a project
  // with no persisted nodes still shows its derived story map.
  const sourceGraph = graph;
  const markGraphDirty = useCallback(() => {
    graphChangeVersionRef.current += 1;
    setDirty(true);
    setSaveErrorAtVersion(-1);
    setSaveTick(graphChangeVersionRef.current);
  }, []);
  useEffect(() => {
    const nextNodes: FlowNode[] = sourceGraph.nodes.map((node) => ({
        id: node.id,
        type: node.type,
        position: node.position,
        data: {
          ...(node.data || {}),
          label: node.label,
          subtitle: node.subtitle,
          type: node.type,
          image_url: node.image_url,
          status: node.status,
          ref_id: node.ref_id ?? null,
          character_id: node.character_id ?? null,
          chapter_id: node.chapter_id ?? null,
          plot_thread_id: node.plot_thread_id ?? null,
          source_refs: node.source_refs || [],
          version: node.version,
        },
      }));
    const nextEdges: FlowEdge[] = sourceGraph.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: compactGraphEdgeLabel(edge.label),
        type: "smoothstep",
        labelShowBg: true,
        labelBgPadding: [7, 4],
        labelBgBorderRadius: 2,
        markerEnd:
          edge.direction === "directed"
            ? { type: MarkerType.ArrowClosed }
            : undefined,
        className: edge.data?.agentDraft ? "agent-draft-edge" : undefined,
        animated: Boolean(edge.data?.agentDraft),
        data: {
          ...(edge.data || {}),
          fullLabel: edge.label,
          kind: edge.kind,
          relation_type: edge.relation_type || edge.kind,
          status: edge.status,
          directed: edge.directed ?? edge.direction === "directed",
          weight: edge.weight,
          source_refs: edge.source_refs || [],
          version: edge.version,
        },
    }));
    setNodes(nextNodes);
    // Keep edges mounted through every streamed patch. React Flow updates the
    // handle coordinates after node measurement; removing or hiding the edge
    // here causes visible flashes and can starve during rapid patch bursts.
    setEdges(nextEdges);
  }, [sourceGraph, setEdges, setNodes]);
  useEffect(() => {
    graphChangeVersionRef.current = 0;
    setSaveTick(0);
    setSaveErrorAtVersion(-1);
    setDirty(false);
    setDeletedEdgeIds([]);
  }, [chapterId]);
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId);
  const persistedEdgeIds = useMemo(
    () =>
      new Set(
        sourceGraph.edges
          .filter((edge) => !edge.data?.agentDraft)
          .map((edge) => edge.id),
      ),
    [sourceGraph.edges],
  );
  useEffect(
    () => setEdgeLabel(selectedEdge ? fullFlowEdgeLabel(selectedEdge) : ""),
    [selectedEdge],
  );
  const saveMutation = useMutation({
    mutationFn: async ({ changeVersion }: { changeVersion: number }) => ({
      saved: await saveStoryGraph(
        projectId,
        chapterId,
        {
          nodes: nodes
            .filter((node) => !node.data.agentDraft)
            .map((node) => ({
              id: node.id,
              type: node.type as StoryGraphNode["type"],
              label: String(node.data.label || "未命名节点"),
              subtitle:
                typeof node.data.subtitle === "string"
                  ? node.data.subtitle
                  : undefined,
              image_url:
                typeof node.data.image_url === "string"
                  ? node.data.image_url
                  : undefined,
              status:
                typeof node.data.status === "string"
                  ? node.data.status
                  : undefined,
              position: node.position,
              data: node.data,
              scope_chapter_id: chapterId,
              ref_id:
                typeof node.data.ref_id === "string"
                  ? node.data.ref_id
                  : undefined,
              character_id:
                typeof node.data.character_id === "string"
                  ? node.data.character_id
                  : undefined,
              chapter_id:
                typeof node.data.chapter_id === "string"
                  ? node.data.chapter_id
                  : undefined,
              plot_thread_id:
                typeof node.data.plot_thread_id === "string"
                  ? node.data.plot_thread_id
                  : undefined,
              source_refs: node.data.source_refs,
              version: node.data.version,
            })),
          edges: edges
            .filter((edge) => !edge.data?.agentDraft)
            .map((edge) => ({
              id: edge.id,
              source: edge.source,
              target: edge.target,
              label: fullFlowEdgeLabel(edge) || undefined,
              kind: String(edge.data?.kind || "relationship"),
              relation_type: String(
                edge.data?.kind || edge.data?.relation_type || "related",
              ),
              direction: edge.markerEnd ? "directed" : "undirected",
              directed: Boolean(edge.markerEnd),
              weight: edge.data?.weight,
              source_refs: edge.data?.source_refs,
              data: edge.data,
              scope_chapter_id: chapterId,
              status:
                (edge.data?.status as StoryGraphEdge["status"]) || "active",
              version: edge.data?.version,
            })),
          version: graph.version,
          layout_version: graph.layout_version,
        },
        { deletedEdgeIds, expectedLayoutVersion: graph.layout_version },
      ),
      changeVersion,
    }),
    onSuccess: ({ saved, changeVersion }) => {
      queryClient.setQueryData(["story-graph", projectId, chapterId], saved);
      if (graphChangeVersionRef.current === changeVersion) {
        setDirty(false);
        setDeletedEdgeIds([]);
      }
    },
    onError: (_error, variables) => {
      setSaveErrorAtVersion(variables.changeVersion);
      notifyFallback(
        onNotice,
        "warning",
        "图谱自动保存失败；本地编辑仍保留，继续修改后会再次保存。",
      );
    },
  });
  useEffect(() => {
    if (
      !dirty ||
      saveMutation.isPending ||
      saveErrorAtVersion === graphChangeVersionRef.current
    ) {
      return undefined;
    }
    const timer = window.setTimeout(
      () => saveMutation.mutate({ changeVersion: graphChangeVersionRef.current }),
      700,
    );
    return () => window.clearTimeout(timer);
  }, [dirty, saveErrorAtVersion, saveMutation.isPending, saveTick]);
  const connect = useCallback(
    (connection: Connection) => {
      if (
        !connection.source ||
        !connection.target ||
        connection.source === connection.target
      )
        return;
      setEdges((current) =>
        addEdge(
          {
            ...connection,
            id: `edge-${Date.now()}`,
            type: "smoothstep",
            label: "新关系",
            markerEnd: { type: MarkerType.ArrowClosed },
            data: {
              kind: "relationship",
              status: "active",
              fullLabel: "新关系",
            },
          },
          current,
        ),
      );
      markGraphDirty();
    },
    [markGraphDirty, setEdges],
  );
  const updateSelectedEdge = () => {
    if (!selectedEdgeId) return;
    setEdges((current) =>
      current.map((edge) =>
        edge.id === selectedEdgeId
          ? {
              ...edge,
              label: compactGraphEdgeLabel(edgeLabel.trim() || "关系"),
              data: {
                ...edge.data,
                fullLabel: edgeLabel.trim() || "关系",
                status: "active",
              },
            }
          : edge,
      ),
    );
    markGraphDirty();
  };
  const patchEdge = (id: string, patch: Partial<FlowEdge>) => {
    setEdges((current) =>
      current.map((edge) => {
        if (edge.id !== id) return edge;
        if (typeof patch.label !== "string") return { ...edge, ...patch };
        return {
          ...edge,
          ...patch,
          label: compactGraphEdgeLabel(patch.label),
          data: { ...edge.data, ...patch.data, fullLabel: patch.label },
        };
      }),
    );
    markGraphDirty();
  };
  const addRelation = (
    source: string,
    target: string,
    kind: string,
    label: string,
    directed: boolean,
    status: StoryGraphEdge["status"],
  ) => {
    setEdges((current) => [
      ...current,
      {
        id: `edge-${Date.now()}`,
        source,
        target,
        label: compactGraphEdgeLabel(label || kind),
        type: "smoothstep",
        markerEnd: directed ? { type: MarkerType.ArrowClosed } : undefined,
        data: {
          kind,
          fullLabel: label || kind,
          status: status === "pending" ? "active" : status,
        },
      },
    ]);
    markGraphDirty();
  };
  const removeEdge = (id: string) => {
    if (edges.find((edge) => edge.id === id)?.data?.agentDraft) return;
    setEdges((current) => current.filter((edge) => edge.id !== id));
    if (persistedEdgeIds.has(id))
      setDeletedEdgeIds((current) =>
        current.includes(id) ? current : [...current, id],
      );
    if (selectedEdgeId === id) setSelectedEdgeId(null);
    markGraphDirty();
  };
  const removeSelectedEdge = () => {
    if (!selectedEdgeId) return;
    removeEdge(selectedEdgeId);
  };
  return (
    <div className="story-graph-shell">
      <div className="story-graph-toolbar">
        <div>
          <span className="eyebrow">故事图谱</span>
          <h2>把人物和情节线牵在一起</h2>
          <small className="story-graph-chapter-scope">当前章节 · {chapterTitle}</small>
        </div>
        <div className="story-graph-actions">
          <div className="view-switch" role="group" aria-label="图谱观察方式">
            <button
              className={graphViewMode === "graph" ? "is-active" : ""}
              onClick={() => setGraphViewMode("graph")}
            >
              <Network size={13} /> 关系图
            </button>
            <button
              className={graphViewMode === "table" ? "is-active" : ""}
              onClick={() => setGraphViewMode("table")}
            >
              <Table2 size={13} /> 关系表
            </button>
          </div>
          <span className={dirty || saveMutation.isPending ? "graph-dirty" : "graph-saved"}>
            {saveMutation.isPending
              ? "自动保存中…"
              : dirty
                ? "等待自动保存"
                : "已自动保存"}
          </span>
        </div>
      </div>
      {graphViewMode === "table" ? (
        <GraphRelationTable
          edges={edges}
          nodes={nodes}
          onPatch={patchEdge}
          onDelete={removeEdge}
          onAdd={addRelation}
        />
      ) : (
        <div className="story-graph-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={graphNodeTypes}
            onNodesChange={(changes) => {
              onNodesChange(changes);
              if (
                changes.some(
                  (change) =>
                    change.type === "position" ||
                    change.type === "remove" ||
                    change.type === "add",
                )
              )
                markGraphDirty();
            }}
            onEdgesChange={(changes) => {
              changes
                .filter((change) => change.type === "remove")
                .forEach((change) => removeEdge(change.id));
              onEdgesChange(changes);
              if (changes.some((change) => change.type !== "select"))
                markGraphDirty();
            }}
            onConnect={connect}
            onNodeClick={(_, node) =>
              onTargetChange?.(graphNodeTarget(node, chapterId))
            }
            onEdgeClick={(_, edge) => {
              setSelectedEdgeId(edge.id);
              onTargetChange?.({
                type: "relationship",
                id: edge.id,
                chapter_id: chapterId,
              });
            }}
            onPaneClick={() =>
              onTargetChange?.({
                type: "chapter",
                id: chapterId,
                chapter_id: chapterId,
              })
            }
            fitView
            minZoom={0.25}
            maxZoom={1.7}
            aria-label="可编辑故事关系图"
          >
            <Background color="rgba(74,84,78,.13)" gap={28} size={1} />
            <Controls showInteractive={false} />
            <MiniMap
              ariaLabel="当前章节图谱缩略图"
              pannable
              zoomable
              maskColor="rgba(79, 117, 107, .12)"
              bgColor="#F7F5EC"
              nodeStrokeColor="#FCFBF5"
              nodeBorderRadius={2}
              style={{ width: 148, height: 96 }}
              nodeColor={(node) =>
                node.type === "character"
                  ? "#A9463B"
                  : node.type === "thread"
                    ? "#4F756B"
                    : "#927A4D"
              }
            />
          </ReactFlow>
          <span className="story-graph-minimap-label" aria-hidden="true">
            章节缩略图
          </span>
          {nodes.length === 0 && (
            <span className="story-graph-minimap-empty" aria-hidden="true">
              本章暂无节点
            </span>
          )}
          {edges.some((edge) => edge.data?.agentDraft) && (
            <div className="story-graph-agent-drafts" aria-label="Agent 图谱草稿">
              {edges
                .filter((edge) => edge.data?.agentDraft)
                .map((edge) => (
                  <AgentGraphEdgeDraft key={edge.id} edge={edge} nodes={nodes} />
                ))}
            </div>
          )}
          {selectedEdge && (
            <aside className="story-edge-inspector">
              <div>
                <span className="eyebrow">关系详情</span>
                <button
                  className="quiet-icon"
                  onClick={() => setSelectedEdgeId(null)}
                  aria-label="关闭关系编辑"
                >
                  <X size={14} />
                </button>
              </div>
              <label className="field">
                <span>关系标签</span>
                <input
                  value={edgeLabel}
                  onChange={(event) => setEdgeLabel(event.target.value)}
                  placeholder="例如：互相试探"
                />
              </label>
              <p>
                标签更新后会自动保存；之后可在关系表里继续补充来源和说明。
              </p>
              <div>
                <button
                  className="button button-secondary button-small"
                  onClick={updateSelectedEdge}
                >
                  <Check size={13} /> 更新标签
                </button>
                <button
                  className="text-button text-danger"
                  onClick={removeSelectedEdge}
                >
                  <Trash2 size={13} /> 删除连线
                </button>
              </div>
            </aside>
          )}
        </div>
      )}
      <div className="story-graph-help">
        <span>
          <Link2 size={13} /> 从节点边缘拖出连线
        </span>
        <span>
          <Table2 size={13} /> 关系表可编辑完整字段
        </span>
        <span>
          <Sparkles size={13} /> 虚线表示 Agent 正在生成
        </span>
      </div>
    </div>
  );
}

function EmptyStoryGraph({
  onCreateChapter,
  onShowCharacters,
}: {
  onCreateChapter: () => void;
  onShowCharacters: () => void;
}) {
  return (
    <div className="studio-empty story-graph-empty">
      <Network size={22} />
      <strong>先为本章铺一张稿纸</strong>
      <p>故事图谱按章节保存。新建稿纸后，就能为本章添加人物、情节节点和关系。</p>
      <div className="manuscript-empty-actions">
        <button
          type="button"
          className="button button-primary"
          onClick={onCreateChapter}
        >
          <Plus size={14} /> 新建稿纸
        </button>
        <button
          type="button"
          className="button button-secondary"
          onClick={onShowCharacters}
        >
          <UserRound size={14} /> 先整理人物
        </button>
      </div>
    </div>
  );
}

function MemoryProposalInbox({
  projectId,
  memoryEpoch,
  onChanged,
}: {
  projectId: string;
  memoryEpoch?: number;
  onChanged: (proposals: AssistantProposal[]) => void | Promise<void>;
}) {
  const queryClient = useQueryClient();
  const proposalsQuery = useQuery({
    queryKey: ["memory-proposals", projectId],
    queryFn: () => listAssistantProposals(projectId),
    staleTime: 10_000,
  });
  const proposals = (proposalsQuery.data || []).filter(
    (proposal) => proposal.status === "proposed",
  );
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState("");
  const handledBatchRef = useRef("");

  useEffect(() => {
    if (syncing || !proposals.length) return;
    const key = proposals.map((proposal) => proposal.id).sort().join("|");
    if (handledBatchRef.current === key) return;
    handledBatchRef.current = key;
    setSyncing(true);
    setNotice("");
    void applyAssistantProposals(
      projectId,
      proposals.map((proposal) => proposal.id),
      {
        expected_memory_epoch:
          proposals[0].base_memory_epoch ?? memoryEpoch,
        expected_versions: Object.fromEntries(
          proposals
            .filter((proposal) => proposal.base_version != null)
            .map((proposal) => [
              proposal.id,
              proposal.base_version as number,
            ]),
        ),
      },
    )
      .then(async ({ proposals: applied }) => {
        queryClient.setQueryData<AssistantProposal[]>(
          ["memory-proposals", projectId],
          (current) =>
            (current || []).map((item) => {
              const next = applied.find(
                (proposal) => proposal.id === item.id,
              );
              return next
                ? {
                    ...item,
                    ...next,
                    patches: next.patches.length
                      ? next.patches
                      : item.patches,
                  }
                : item;
            }),
        );
        await onChanged(applied);
        setNotice(`已自动同步 ${applied.length || proposals.length} 项故事资料。`);
      })
      .catch((error) => {
        setNotice(
          error instanceof Error
            ? error.message
            : "故事资料自动同步失败，请刷新后重试。",
        );
      })
      .finally(() => setSyncing(false));
  }, [memoryEpoch, onChanged, projectId, proposals, queryClient, syncing]);

  if (!proposalsQuery.isLoading && !proposals.length && !syncing && !notice) {
    return null;
  }
  return (
    <section className="memory-proposal-inbox memory-auto-sync" aria-live="polite">
      <span className="memory-auto-sync-mark">
        {syncing || proposalsQuery.isLoading ? (
          <Loader2 size={14} className="spin" />
        ) : (
          <CheckCircle2 size={14} />
        )}
      </span>
      <div>
        <strong>
          {syncing || proposalsQuery.isLoading
            ? "正在自动同步故事资料"
            : "故事资料已自动同步"}
        </strong>
        <small>
          {notice || "人物、关系和情节线会直接纳入项目，无需逐项确认。"}
        </small>
      </div>
    </section>
  );
}
function textHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/**
 * Keep provider-side structured output out of the conversation transcript.
 *
 * Newer workers persist the natural-language reply separately from
 * proposals, but conversations created by an older worker (or by a gateway
 * that ignored the two-pass contract) can still contain a JSON/YAML
 * `proposals` tail. The proposal cards below are the review surface, so
 * rendering that machine payload in the assistant bubble only adds noise and
 * can expose implementation details to the author.
 */
export function visibleAgentMessage(content: string): string {
  const raw = String(content || "").trim();
  if (!raw) return "";

  const fenced = raw.match(/^```(?:json|jsonc)?\s*([\s\S]*?)\s*```$/i);
  const candidate = fenced?.[1]?.trim() || raw;
  try {
    const parsed = JSON.parse(candidate) as unknown;
    if (parsed && typeof parsed === "object") {
      const envelope = parsed as Record<string, unknown>;
      const reply = envelope.reply ?? envelope.message;
      if (
        typeof reply === "string" &&
        ("proposals" in envelope || "changes" in envelope)
      ) {
        return visibleAgentMessage(reply);
      }
    }
  } catch {
    // Some old provider responses are YAML-like or truncated JSON. The line
    // based fallback below still removes their proposals section safely.
  }

  const proposalsStart = raw.search(
    /(?:^|\r?\n)\s*["']?proposals?["']?\s*:/i,
  );
  if (proposalsStart >= 0) {
    const visibleLines = raw.slice(0, proposalsStart).trim().split(/\r?\n/);
    const finalLine = visibleLines[visibleLines.length - 1] || "";
    if (
      /(?:如果|若|如需|如有|when|if).*(?:提交|返回|提供|生成|结构化|申请|提案|proposal|change)/i.test(
        finalLine,
      )
    ) {
      visibleLines.pop();
    }
    return visibleLines.join("\n").trim();
  }
  return raw;
}

function assistantMessageText(content: string): string {
  const visible = visibleAgentMessage(content);
  return visible || (String(content || "").trim() ? "已生成改动，正在自动写入。" : "");
}

type LiveSendState = {
  conversationId: string;
  baselineSequence: number;
  baselineProposalIds: Set<string>;
  runId: string;
  followedProposalIds: Set<string>;
  bufferedProposals: Map<string, AssistantProposal>;
};

type LiveEventMatch = "current" | "awaiting-run" | "stale";

export function classifyLiveAgentEvent(
  event: Pick<AssistantEvent, "sequence" | "run_id">,
  conversationId: string,
  pending: Pick<LiveSendState, "conversationId" | "baselineSequence" | "runId"> | null,
  knownRunIds: ReadonlySet<string>,
): LiveEventMatch {
  if (!pending || pending.conversationId !== conversationId) return "stale";
  if (
    event.sequence > 0 &&
    event.sequence <= pending.baselineSequence
  ) {
    return "stale";
  }
  if (pending.runId) {
    return event.run_id && event.run_id !== pending.runId
      ? "stale"
      : "current";
  }
  if (event.run_id) {
    // A reconnect may replay a completed run after a new send started.
    // Runs loaded before the send are never allowed to move the workspace.
    return knownRunIds.has(event.run_id) ? "stale" : "current";
  }
  // Some older event rows omitted run_id. Hold their proposals until the POST
  // response supplies the authoritative run id instead of following a
  // historical replay immediately.
  return "awaiting-run";
}

function canFollowAgentProposal(proposal: AssistantProposal) {
  const operation = String(proposal.operation || "").toLowerCase();
  const targetType = String(
    proposal.target_type || proposal.target.type || "",
  ).toLowerCase();
  return (
    targetType.includes("chapter") ||
    targetType.includes("character") ||
    targetType.includes("thread") ||
    targetType.includes("relation") ||
    targetType.includes("graph") ||
    operation.includes("chapter") ||
    operation.includes("character") ||
    operation.includes("graph") ||
    operation.includes("relation")
  );
}

function proposalUpdatePatches(
  proposal: AssistantProposal,
  overrides?: Record<string, unknown>,
): AgentDraftPatch[] {
  return proposal.patches.map((patch) => {
    const path = patch.path;
    return {
      op: "replace",
      path,
      label: patch.label,
      value: overrides && Object.prototype.hasOwnProperty.call(overrides, path)
        ? overrides[path]
        : patch.value,
    };
  });
}

function diffPreviewText(value: string) {
  const clean = value.trim();
  return clean.length > 2400 ? `${clean.slice(0, 2400)}\n……` : clean || "（空白）";
}

function GlobalDiffWorkspace({
  project,
  chapters,
  proposals,
  onInspectChapter,
}: {
  project: Project;
  chapters: Chapter[];
  proposals: AssistantProposal[];
  onInspectChapter: (chapter: Chapter) => void;
}) {
  const [submitting, setSubmitting] = useState<"apply" | "reject" | "">("");
  const rows = useMemo(
    () =>
      proposals
        .map((proposal) => {
          const chapterId = String(
            proposal.target_id ||
              proposal.scope_chapter_id ||
              proposal.target.chapter_id ||
              proposal.target.id ||
              "",
          );
          const chapter = chapters.find((item) => item.id === chapterId) || null;
          const draft = chapter
            ? chapterAgentDraft(chapter, chapter.content || "", [proposal])
            : null;
          return { proposal, chapter, draft };
        })
        .sort((left, right) => {
          const leftOrder = left.chapter?.number ?? Number.MAX_SAFE_INTEGER;
          const rightOrder = right.chapter?.number ?? Number.MAX_SAFE_INTEGER;
          return leftOrder - rightOrder;
        }),
    [chapters, proposals],
  );
  useEffect(() => {
    if (!proposals.length) setSubmitting("");
  }, [proposals.length]);
  const ready = rows.length > 0 && rows.every(({ proposal }) => proposal.status === "proposed");
  const act = (action: "apply" | "reject") => {
    if (!ready || submitting) return;
    setSubmitting(action);
    window.dispatchEvent(
      new CustomEvent("story-studio-global-diff-action", {
        detail: {
          projectId: project.id,
          action,
          proposalIds: rows.map(({ proposal }) => proposal.id),
        },
      }),
    );
  };
  return (
    <section className="global-diff-workspace" aria-label="全书改动 Diff">
      <header className="global-diff-head">
        <div>
          <span className="eyebrow">全书协作校样</span>
          <h2>逐章查看 Agent 的改动</h2>
          <p>
            Agent 按章节顺序工作。这里的内容尚未进入正式正文，接受后才会统一写入并整理新的全书记忆。
          </p>
        </div>
        {rows.length > 0 && (
          <div className="global-diff-actions">
            <button
              type="button"
              className="button button-secondary"
              onClick={() => act("reject")}
              disabled={!ready || Boolean(submitting)}
            >
              <X size={14} /> {submitting === "reject" ? "正在拒绝…" : "全部拒绝"}
            </button>
            <button
              type="button"
              className="button button-primary"
              onClick={() => act("apply")}
              disabled={!ready || Boolean(submitting)}
            >
              <Check size={14} /> {submitting === "apply" ? "正在接受…" : "全部接受"}
            </button>
          </div>
        )}
      </header>
      {rows.length ? (
        <div className="global-diff-ledger">
          <div className="global-diff-sequence" aria-label="章节处理顺序">
            {rows.map(({ proposal, chapter }, index) => (
              <span
                key={proposal.id}
                className={proposal.status === "building" ? "is-writing" : "is-ready"}
              >
                <b>{String(index + 1).padStart(2, "0")}</b>
                {chapter ? `第 ${chapter.number} 章` : "全书设定"}
              </span>
            ))}
          </div>
          {rows.map(({ proposal, chapter, draft }, index) => (
            <article
              id={`global-diff-${proposal.id}`}
              className={`global-diff-sheet ${proposal.status === "building" ? "is-writing" : "is-ready"}`}
              key={proposal.id}
            >
              <header>
                <span className="global-diff-order">{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <small>{chapter ? `第 ${chapter.number} 章` : "全书资料"}</small>
                  <strong>{chapter?.title || proposal.summary}</strong>
                </div>
                <span className="global-diff-state">
                  {proposal.status === "building" ? "Agent 正在修改" : "等待整批确认"}
                </span>
                {chapter && (
                  <button type="button" onClick={() => onInspectChapter(chapter)}>
                    打开稿纸 <ArrowRight size={12} />
                  </button>
                )}
              </header>
              {draft ? (
                <div className="global-diff-columns">
                  <section>
                    <span>修改前</span>
                    <pre>{diffPreviewText(draft.before)}</pre>
                  </section>
                  <section>
                    <span>修改后</span>
                    <pre>{diffPreviewText(draft.after)}</pre>
                  </section>
                </div>
              ) : (
                <div className="global-setting-diff">
                  {proposal.patches.map((patch) => (
                    <div key={`${proposal.id}-${patch.path}`}>
                      <span>{patch.label || patch.path}</span>
                      <ins>
                        {typeof patch.value === "string"
                          ? patch.value
                          : JSON.stringify(patch.value, null, 2)}
                      </ins>
                    </div>
                  ))}
                </div>
              )}
            </article>
          ))}
        </div>
      ) : (
        <div className="global-diff-empty">
          <FileDiff size={24} />
          <strong>还没有待确认的全书改动</strong>
          <p>在右侧切换到“全书协作”，告诉 Agent 需要贯穿哪些章节检查或修改。</p>
        </div>
      )}
    </section>
  );
}

function AgentLiveBuildRail({
  summary,
  activeSurface,
  workMode,
  onShowCharacters,
  onShowGraph,
}: {
  summary: AgentBuildSummary;
  activeSurface: "characters" | "graph" | null;
  workMode: AgentWorkMode;
  onShowCharacters: () => void;
  onShowGraph: () => void;
}) {
  if (!summary.total) return null;
  const graphCount = summary.nodeCount + summary.edgeCount;
  return (
    <section
      className={`agent-live-build${summary.building ? " is-building" : " is-ready"}`}
      aria-label="Agent 实时制作进度"
      aria-live="polite"
    >
      <span className="agent-live-build-mark" aria-hidden="true">
        <Sparkles size={15} />
      </span>
      <div className="agent-live-build-copy">
        <span>
          {summary.building
            ? "实时制作中"
            : workMode === "global"
              ? "本批生成完成"
              : "正在自动保存"}
        </span>
        <strong>
          {summary.building
            ? "Agent 正在把内容写进草稿"
            : workMode === "global"
              ? "内容已生成，正在等待整批确认"
              : "内容已生成，正在写入项目"}
        </strong>
        <small>
          {summary.chapterCount ? `正文 ${summary.chapterCount} · ` : ""}
          人物 {summary.characterCount} · 节点 {summary.nodeCount} · 关系 {summary.edgeCount} · 已写入 {summary.patchCount} 项
        </small>
      </div>
      <div className="agent-live-build-controls">
        <div className="agent-live-build-switch" role="group" aria-label="查看 Agent 制作内容">
          <button
            type="button"
            className={activeSurface === "characters" ? "is-active" : ""}
            onClick={onShowCharacters}
            disabled={!summary.characterCount}
          >
            <UserRound size={13} /> 人物卡 <b>{summary.characterCount}</b>
          </button>
          <button
            type="button"
            className={activeSurface === "graph" ? "is-active" : ""}
            onClick={onShowGraph}
            disabled={!graphCount && !summary.graphProposalCount}
          >
            <Network size={13} /> 故事图谱 <b>{graphCount}</b>
          </button>
        </div>
      </div>
      <span
        className="agent-live-build-progress"
        role="progressbar"
        aria-label={summary.building ? "Agent 正在生成内容" : "Agent 内容生成进度"}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={summary.building ? undefined : 100}
      >
        <i />
      </span>
    </section>
  );
}

function AgentDock({
  project,
  target,
  character,
  chapters,
  activeChapter,
  activeContent,
  onChapter,
  assistantProvider,
  onProposalPreview,
  onProposalDismiss,
  onFollowProposal,
  onProposalApplied,
  relationNodeCount = 0,
  mobileVisible = true,
  autoOpen = false,
  onNotice,
  workMode,
  onWorkModeChange,
  onOpenGlobalDiff,
  onMemoryRun,
}: {
  project: Project;
  target: AgentTarget;
  character: CharacterCard | null;
  chapters: Chapter[];
  activeChapter: Chapter | null;
  activeContent: string;
  onChapter: (chapter: Chapter) => void;
  assistantProvider?: ProviderProfile | null;
  onProposalPreview: (proposal: AssistantProposal) => void;
  onProposalDismiss: (proposalId: string) => void;
  onFollowProposal?: (proposal: AssistantProposal) => void;
  onProposalApplied: (
    proposal: AssistantProposal | AssistantProposal[],
  ) => void | Promise<void>;
  relationNodeCount?: number;
  mobileVisible?: boolean;
  autoOpen?: boolean;
  onNotice?: StoryStudioProps["onNotice"];
  workMode: AgentWorkMode;
  onWorkModeChange: (mode: AgentWorkMode) => void;
  onOpenGlobalDiff: () => void;
  onMemoryRun?: (run: MemoryRun) => void;
}) {
  const queryClient = useQueryClient();
  const [conversation, setConversation] =
    useState<AssistantConversation | null>(null);
  const conversationRef = useRef<AssistantConversation | null>(null);
  useEffect(() => {
    conversationRef.current = conversation;
  }, [conversation]);
  const [messages, setMessages] = useState<AssistantConversation["messages"]>(
    [],
  );
  const [proposals, setProposals] = useState<AssistantProposal[]>([]);
  const proposalsRef = useRef<AssistantProposal[]>([]);
  useEffect(() => {
    proposalsRef.current = proposals;
  }, [proposals]);
  const [message, setMessage] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [status, setStatus] = useState<AssistantConversation["status"]>("idle");
  const [liveOutputStarted, setLiveOutputStarted] = useState(false);
  const [busyProposal, setBusyProposal] = useState("");
  const [notice, setNotice] = useState(
    autoOpen ? "Agent 已就位，可以从右侧开始描述你的想法。" : "",
  );
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historySearch, setHistorySearch] = useState("");
  const [historyMode, setHistoryMode] = useState<"all" | AgentWorkMode>("all");
  const [allowImage, setAllowImage] = useState(false);
  const [failedRunId, setFailedRunId] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [currentRunId, setCurrentRunId] = useState("");
  const [stopping, setStopping] = useState(false);
  const [activeStage, setActiveStage] = useState("");
  const sequenceRef = useRef(0);
  const streamingMessageIdRef = useRef("");
  const streamingTextRef = useRef("");
  const lastAssistantReplyRef = useRef("");
  const knownAssistantMessageIdsRef = useRef<Set<string>>(new Set());
  const followProposalRef = useRef(onFollowProposal);
  const activeRunIdRef = useRef("");
  const knownRunIdsRef = useRef<Set<string>>(new Set());
  const liveSendRef = useRef<LiveSendState | null>(null);
  const conversationCursorIdRef = useRef("");
  const autoApplyBatchRef = useRef("");
  const lastProposalActivityRef = useRef(0);
  const initializedHistory = useRef(false);
  const agentIsBusy =
    status === "queued" ||
    status === "running" ||
    status === "streaming" ||
    (status === "reconnecting" && Boolean(currentRunId));
  useEffect(() => {
    followProposalRef.current = onFollowProposal;
  }, [onFollowProposal]);
  const beginLiveSend = useCallback(
    (
      conversationId: string,
      baselineSequence: number,
      baselineProposalIds: Iterable<string>,
    ) => {
      liveSendRef.current = {
        conversationId,
        baselineSequence,
        baselineProposalIds: new Set(baselineProposalIds),
        runId: "",
        followedProposalIds: new Set(),
        bufferedProposals: new Map(),
      };
      activeRunIdRef.current = "";
      setCurrentRunId("");
    },
    [],
  );
  const activateLiveRun = useCallback((conversationId: string, runId: string) => {
    if (!runId) return;
    const pending = liveSendRef.current;
    if (!pending || pending.conversationId !== conversationId) return;
    pending.runId = runId;
    activeRunIdRef.current = runId;
    setCurrentRunId(runId);
    knownRunIdsRef.current.add(runId);
    const buffered = [...pending.bufferedProposals.values()];
    pending.bufferedProposals.clear();
    buffered.forEach((proposal) => {
      if (pending.followedProposalIds.size > 0) return;
      if (!canFollowAgentProposal(proposal)) return;
      if (pending.followedProposalIds.has(proposal.id)) return;
      pending.followedProposalIds.add(proposal.id);
      followProposalRef.current?.(proposal);
    });
  }, []);
  const matchLiveEvent = useCallback(
    (event: AssistantEvent, conversationId: string): LiveEventMatch => {
      const pending = liveSendRef.current;
      const match = classifyLiveAgentEvent(
        event,
        conversationId,
        pending,
        knownRunIdsRef.current,
      );
      if (match === "current" && pending && !pending.runId && event.run_id) {
        if (knownRunIdsRef.current.has(event.run_id)) return "stale";
        pending.runId = event.run_id;
        activeRunIdRef.current = event.run_id;
        setCurrentRunId(event.run_id);
      }
      return match;
    },
    [],
  );
  const maybeFollowLiveProposal = useCallback(
    (
      proposal: AssistantProposal,
      event: AssistantEvent,
      conversationId: string,
    ) => {
      if (!canFollowAgentProposal(proposal)) return;
      const match = matchLiveEvent(event, conversationId);
      if (match === "stale") return;
      const pending = liveSendRef.current;
      if (!pending) return;
      if (match === "awaiting-run") {
        pending.bufferedProposals.set(proposal.id, proposal);
        return;
      }
      // A run can create characters, nodes and relationships together. Keep
      // the first relevant surface steady so the author can watch fields
      // arrive instead of being bounced between tabs for every proposal.
      if (pending.followedProposalIds.size > 0) return;
      if (pending.followedProposalIds.has(proposal.id)) return;
      pending.followedProposalIds.add(proposal.id);
      followProposalRef.current?.(proposal);
    },
    [matchLiveEvent],
  );
  const conversationsQuery = useQuery({
    queryKey: ["assistant-conversations", project.id],
    queryFn: () => listAssistantConversations(project.id),
    staleTime: 15_000,
  });
  const conversationRows = conversationsQuery.data || [];
  const filteredConversationRows = useMemo(() => {
    const query = historySearch.trim().toLocaleLowerCase();
    return conversationRows.filter((row) => {
      if (
        historyMode !== "all" &&
        conversationWorkMode(row.purpose) !== historyMode
      ) {
        return false;
      }
      return (
        !query ||
        (row.title || "新的写作对话").toLocaleLowerCase().includes(query)
      );
    });
  }, [conversationRows, historyMode, historySearch]);
  const rememberedTurns = messages.filter((item) => item.role === "user").length;

  const loadConversation = useCallback(
    async (conversationId: string) => {
      try {
        if (conversationCursorIdRef.current !== conversationId) {
          sequenceRef.current = 0;
          conversationCursorIdRef.current = conversationId;
        }
        const metadata = await getAssistantConversation(
          project.id,
          conversationId,
        );
        const loadedMessages = await listAssistantMessages(
          project.id,
          conversationId,
        );
        const inferredTarget =
          [...loadedMessages].reverse().find((item) => item.target)?.target ||
          target;
        const loadedProposals = metadata.proposals?.length
          ? metadata.proposals
          : await listAssistantProposals(project.id, conversationId);
        const loaded = {
          ...metadata,
          target: inferredTarget,
          messages: loadedMessages,
          proposals: loadedProposals,
        };
        conversationRef.current = loaded;
        setConversation(loaded);
        onWorkModeChange(conversationWorkMode(loaded.purpose));
        setMessages(loadedMessages);
        streamingMessageIdRef.current = "";
        streamingTextRef.current = "";
        setStreamingText("");
        setLiveOutputStarted(false);
        knownAssistantMessageIdsRef.current = new Set(
          loadedMessages
            .filter((item) => item.role === "assistant")
            .map((item) => item.id),
        );
        lastAssistantReplyRef.current =
          [...loadedMessages]
            .reverse()
            .find((item) => item.role === "assistant" && item.content.trim())
            ?.content || "";
        proposalsRef.current
          .filter(
            (proposal) =>
              !loadedProposals.some((loadedProposal) => loadedProposal.id === proposal.id),
          )
          .forEach((proposal) => onProposalDismiss(proposal.id));
        proposalsRef.current = loadedProposals;
        setProposals(loadedProposals);
        loadedProposals
          .filter((proposal) => proposal.status === "proposed")
          .forEach(onProposalPreview);
        const runs = await listAssistantRuns(project.id, conversationId).catch(
          () => [],
        );
        const latestRun = runs.at(-1);
        knownRunIdsRef.current = new Set(
          runs.map((run) => run.id).filter(Boolean),
        );
        const latestIsBusy =
          latestRun?.status === "queued" || latestRun?.status === "running";
        activeRunIdRef.current = latestIsBusy ? latestRun?.id || "" : "";
        setCurrentRunId(activeRunIdRef.current);
        setStatus(
          latestIsBusy
            ? latestRun?.status === "queued"
              ? "queued"
              : "running"
            : latestRun?.status === "cancelled"
              ? "cancelled"
            : latestRun?.status === "failed" ||
                latestRun?.status === "needs_retry"
              ? "error"
              : "idle",
        );
        setActiveStage(latestIsBusy ? latestRun?.stage || "queued" : "");
        setFailedRunId(
          latestRun &&
            (latestRun.status === "failed" ||
              latestRun.status === "needs_retry")
            ? latestRun.id
            : "",
        );
        if (latestRun?.status === "cancelled") {
          setNotice("上次任务已停止，你可以沿着这段对话继续说。");
        }
      } catch (error) {
        notifyFallback(
          onNotice,
          "warning",
          error instanceof Error ? error.message : "历史会话读取失败。",
        );
      }
    },
    [onNotice, onProposalDismiss, onProposalPreview, onWorkModeChange, project.id, target],
  );

  useEffect(() => {
    if (initializedHistory.current) return;
    // A newly created conversation is already authoritative. The history
    // query invalidation that follows its first message must not reload that
    // same row and overwrite the still-running run id with a list snapshot.
    if (conversation?.id) {
      initializedHistory.current = true;
      return;
    }
    if (!conversationRows.length) return;
    initializedHistory.current = true;
    void loadConversation(conversationRows[0].id);
  }, [conversation?.id, conversationRows, loadConversation]);

  useEffect(() => {
    if (!historyOpen) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setHistoryOpen(false);
      setHistorySearch("");
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [historyOpen]);

  useEffect(() => {
    const openConversation = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          projectId?: string;
          conversationId?: string;
        }>
      ).detail;
      if (
        detail?.projectId !== project.id ||
        !detail.conversationId
      ) {
        return;
      }
      setHistoryOpen(false);
      void loadConversation(detail.conversationId);
    };
    window.addEventListener(
      "story-studio-open-conversation",
      openConversation,
    );
    return () =>
      window.removeEventListener(
        "story-studio-open-conversation",
        openConversation,
      );
  }, [loadConversation, project.id]);

  useEffect(() => {
    const openProposal = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          projectId?: string;
          proposalId?: string;
        }>
      ).detail;
      if (detail?.projectId !== project.id || !detail.proposalId) return;
      setHistoryOpen(false);
      void listAssistantProposals(project.id)
        .then((rows) => {
          const selected = rows.find((item) => item.id === detail.proposalId);
          if (!selected) {
            notifyFallback(onNotice, "warning", "这条改动已经自动处理或不存在。");
            return;
          }
          setProposals((current) => [
            selected,
            ...current.filter((item) => item.id !== selected.id),
          ]);
          onProposalPreview(selected);
          followProposalRef.current?.(selected);
          setNotice("已定位这条 Agent 改动，系统会自动写入项目。");
        })
        .catch((error) =>
          notifyFallback(
            onNotice,
            "warning",
            error instanceof Error ? error.message : "Agent 改动读取失败。",
          ),
        );
    };
    window.addEventListener("story-studio-open-proposal", openProposal);
    return () =>
      window.removeEventListener("story-studio-open-proposal", openProposal);
  }, [onNotice, onProposalPreview, project.id]);

  useEffect(() => {
    if (!conversation?.id) return undefined;
    const conversationId = conversation.id;
    const cleanup = listenAssistantEvents(
      project.id,
      conversationId,
      (event: AssistantEvent) => {
        if (event.sequence && event.sequence <= sequenceRef.current) return;
        if (event.sequence) sequenceRef.current = event.sequence;
        const liveEventMatch = matchLiveEvent(event, conversationId);
        if (liveEventMatch === "current" && event.run_id) {
          activeRunIdRef.current = event.run_id;
          setCurrentRunId(event.run_id);
        }
        if (event.type === "message_delta") {
          // A replay can include deltas for a message that was already loaded
          // from the durable transcript. Keep the message card as the single
          // source of truth in that case; otherwise the same reply briefly
          // appears once as history and once as a streaming bubble.
          if (
            event.message_id &&
            knownAssistantMessageIdsRef.current.has(event.message_id)
          ) {
            setLiveOutputStarted(true);
            setStatus("streaming");
            if (event.run_id) setFailedRunId("");
            return;
          }
          streamingMessageIdRef.current = event.message_id || "";
          if (event.delta) setLiveOutputStarted(true);
          setStreamingText((current) => {
            const next = current + event.delta;
            streamingTextRef.current = next;
            return next;
          });
          setStatus("streaming");
          if (event.run_id) setFailedRunId("");
        } else if (event.type === "message_replace") {
          const messageId =
            event.message_id || `assistant-${event.run_id || event.sequence}`;
          streamingMessageIdRef.current = messageId;
          streamingTextRef.current = "";
          setStreamingText("");
          setLiveOutputStarted(Boolean(event.content.trim()));
          // An empty replace is the worker's retry/reset frame. Allow the
          // following deltas through even when this message id was present in
          // the durable transcript loaded before the retry started.
          if (event.content.trim()) {
            knownAssistantMessageIdsRef.current.add(messageId);
          } else {
            knownAssistantMessageIdsRef.current.delete(messageId);
          }
          lastAssistantReplyRef.current = event.content;
          setMessages((current) => {
            const exists = current.some((item) => item.id === messageId);
            return exists
              ? current.map((item) =>
                  item.id === messageId
                    ? { ...item, content: event.content, status: "completed" }
                    : item,
                )
              : [
                  ...current,
                  {
                    id: messageId,
                    role: "assistant",
                    content: event.content,
                    status: "completed",
                  },
                ];
          });
        } else if (event.type === "message_completed") {
          streamingMessageIdRef.current = "";
          streamingTextRef.current = "";
          setStreamingText("");
          setLiveOutputStarted(false);
          setStatus("idle");
          setActiveStage("");
          activeRunIdRef.current = "";
          setCurrentRunId("");
          void loadConversation(conversationId);
          const completedSend =
            liveEventMatch === "current" ? liveSendRef.current : null;
          if (completedSend && (event.proposal_count || 0) > 0) {
            // A fast extractor can persist all proposal frames between two
            // browser paints. Re-read the durable rows and reveal the first
            // new surface even if that burst was coalesced during reconnect.
            void listAssistantProposals(project.id, conversationId)
              .then((rows) => {
                if (
                  liveSendRef.current &&
                  liveSendRef.current !== completedSend
                ) {
                  return;
                }
                const fresh = rows.filter(
                  (proposal) =>
                    isPreviewProposal(proposal) &&
                    !completedSend.baselineProposalIds.has(proposal.id),
                );
                fresh.forEach((proposal) => onProposalPreview(proposal));
                const first = fresh.find(canFollowAgentProposal);
                if (
                  first &&
                  completedSend.followedProposalIds.size === 0
                ) {
                  completedSend.followedProposalIds.add(first.id);
                  followProposalRef.current?.(first);
                }
              })
              .catch(() => undefined);
          }
        } else if (event.type === "proposal_created") {
          lastProposalActivityRef.current = Date.now();
          autoApplyBatchRef.current = "";
          setLiveOutputStarted(true);
          const nextProposals = [
            ...proposalsRef.current.filter(
              (item) => item.id !== event.proposal.id,
            ),
            event.proposal,
          ];
          proposalsRef.current = nextProposals;
          setProposals(nextProposals);
          onProposalPreview(event.proposal);
          // Global requests can create people and relationships while the
          // author is still looking at a blank manuscript. Move the central
          // workspace with the live proposal stream so those pale-teal drafts
          // are visible without hunting for a secondary action. The matcher
          // gates this to the current send's run/cursor, not history replay.
          maybeFollowLiveProposal(event.proposal, event, conversationId);
        } else if (event.type === "proposal_patch") {
          lastProposalActivityRef.current = Date.now();
          autoApplyBatchRef.current = "";
          setLiveOutputStarted(true);
          const existing = proposalsRef.current.find(
            (proposal) => proposal.id === event.proposal_id,
          );
          const base =
            existing ||
            ({
              id: event.proposal_id,
              conversation_id: conversationId,
              target: event.target || target,
              summary: "Agent 正在整理提案",
              patches: [],
              status: "building",
              target_type: event.target?.type,
              target_id: event.target?.id || undefined,
            } satisfies AssistantProposal);
          const preview = proposalWithPatch(base, event.patch);
          const nextProposals = [
            ...proposalsRef.current.filter(
              (proposal) => proposal.id !== event.proposal_id,
            ),
            preview,
          ];
          proposalsRef.current = nextProposals;
          setProposals(nextProposals);
          onProposalPreview(preview);
          maybeFollowLiveProposal(preview, event, conversationId);
        } else if (event.type === "proposal_completed") {
          lastProposalActivityRef.current = Date.now();
          autoApplyBatchRef.current = "";
          const completedProposal = proposalsRef.current.find(
            (proposal) => proposal.id === event.proposal_id,
          );
          if (completedProposal) {
            const next = {
              ...completedProposal,
              status: "proposed" as const,
            };
            const nextProposals = proposalsRef.current.map((proposal) =>
              proposal.id === event.proposal_id ? next : proposal,
            );
            proposalsRef.current = nextProposals;
            setProposals(nextProposals);
            onProposalPreview(next);
          }
          // Patch events are the live draft stream. Once the producer marks a
          // proposal ready, fetch the durable row once to pick up metadata
          // that was intentionally omitted from the skeleton/patch frames.
          void listAssistantProposals(project.id, conversationId)
            .then((rows) => {
              const next = rows.find(
                (proposal) => proposal.id === event.proposal_id,
              );
              if (!next) return;
              const nextProposals = [
                ...proposalsRef.current.filter(
                  (proposal) => proposal.id !== next.id,
                ),
                next,
              ];
              proposalsRef.current = nextProposals;
              setProposals(nextProposals);
              onProposalPreview(next);
              maybeFollowLiveProposal(next, event, conversationId);
            })
            .catch(() => undefined);
          if (
            !activeRunIdRef.current ||
            (event.run_id && event.run_id !== activeRunIdRef.current)
          ) {
            setStatus("idle");
          }
        } else if (event.type === "status") {
          setStatus(event.status);
          if (event.stage) setActiveStage(event.stage);
          if (event.message) {
            const noticeText = visibleAgentMessage(event.message);
            const sameAsReply =
              Boolean(noticeText) &&
              (visibleAgentMessage(lastAssistantReplyRef.current) ===
                noticeText ||
                visibleAgentMessage(streamingTextRef.current) === noticeText);
            if (!sameAsReply) setNotice(event.message);
          }
          if (event.status === "idle") {
            const pendingLiveSend = liveSendRef.current;
            if (
              (!event.run_id && !pendingLiveSend?.runId) ||
              event.run_id === activeRunIdRef.current
            ) {
              activeRunIdRef.current = "";
              setCurrentRunId("");
              liveSendRef.current = null;
            }
            setFailedRunId("");
            streamingMessageIdRef.current = "";
            streamingTextRef.current = "";
            setStreamingText("");
            setLiveOutputStarted(false);
            setActiveStage("");
            void loadConversation(conversationId);
          } else if (event.status === "cancelled") {
            activeRunIdRef.current = "";
            setCurrentRunId("");
            liveSendRef.current = null;
            streamingMessageIdRef.current = "";
            streamingTextRef.current = "";
            setStreamingText("");
            setLiveOutputStarted(false);
            setActiveStage("");
          }
        } else if (event.type === "error") {
          setStatus("error");
          setFailedRunId(event.run_id || "");
          setNotice(
            event.message || "Agent 本次任务没有完成，可以从原位置继续重试。",
          );
        }
      },
      () => {
        if (!activeRunIdRef.current) return;
        setStatus((current) =>
          current === "error" ? current : "reconnecting",
        );
        setNotice("正在恢复实时连接，已收到的内容不会丢失…");
      },
      sequenceRef.current,
      () => {
        setStatus((current) => (current === "reconnecting" ? "idle" : current));
        setNotice((current) =>
          current.startsWith("正在恢复实时连接") ? "实时连接已恢复。" : current,
        );
      },
    );
    return cleanup;
  }, [conversation?.id, loadConversation, onProposalPreview, project.id]);

  const startNewConversation = (nextMode: AgentWorkMode = workMode) => {
    proposals.forEach((proposal) => onProposalDismiss(proposal.id));
    setConversation(null);
    conversationRef.current = null;
    setMessages([]);
    proposalsRef.current = [];
    setProposals([]);
    setStreamingText("");
    setLiveOutputStarted(false);
    setStatus("idle");
    setFailedRunId("");
    setActiveStage("");
    streamingMessageIdRef.current = "";
    streamingTextRef.current = "";
    lastAssistantReplyRef.current = "";
    knownAssistantMessageIdsRef.current = new Set();
    activeRunIdRef.current = "";
    setCurrentRunId("");
    knownRunIdsRef.current = new Set();
    liveSendRef.current = null;
    conversationCursorIdRef.current = "";
    onWorkModeChange(nextMode);
    setNotice(
      nextMode === "global"
        ? "新的全书协作已准备好。改动会按章节进入左侧 Diff。"
        : "新的章节对话已准备好。Agent 会自动跟随当前章节。",
    );
    setHistoryOpen(false);
    setHistorySearch("");
    sequenceRef.current = 0;
  };
  const ensureConversation = async () => {
    const current = conversationRef.current || conversation;
    if (current && conversationWorkMode(current.purpose) === workMode) {
      return current;
    }
    const conversationTarget: AgentTarget =
      workMode === "global"
        ? { type: "project", id: project.id, chapter_id: null }
        : resolveAgentMessageTarget(target, "chapter", activeChapter);
    const created = await createAssistantConversation(project.id, {
      target: conversationTarget,
      purpose: workMode,
      title: "新的写作对话",
    });
    const loaded = {
      ...created,
      target: conversationTarget,
      messages: created.messages || [],
      proposals: created.proposals || [],
    };
    // Event sequences are scoped to a conversation. Reusing the previous
    // conversation's cursor can discard the beginning of a newly-created
    // stream (including run.started and the first live draft patches).
    sequenceRef.current = 0;
    conversationCursorIdRef.current = created.id;
    conversationRef.current = loaded;
    setConversation(loaded);
    setMessages((current) => {
      // Conversation creation returns before the POST that records the first
      // user message. Preserve that optimistic bubble while the server starts
      // the run, then let the durable response replace it on completion.
      const pending = current.filter((item) => item.id.startsWith("local-"));
      return [...loaded.messages, ...pending];
    });
    knownAssistantMessageIdsRef.current = new Set(
      loaded.messages
        .filter((item) => item.role === "assistant")
        .map((item) => item.id),
    );
    setProposals(loaded.proposals);
    queryClient.invalidateQueries({
      queryKey: ["assistant-conversations", project.id],
    });
    return loaded;
  };
  const contextSnapshot = (
    chapter: Chapter | null = activeChapter,
    writeIntent = false,
  ): AgentContextSnapshot => {
    return {
      chapter_id: workMode === "global" ? null : chapter?.id || null,
      base_revision_id: workMode === "global" ? null : chapter?.revision_id || null,
      selection: null,
      agent_write_intent: writeIntent,
      agent_mode: workMode,
    };
  };
  const send = async () => {
    const content = message.trim();
    if (!content || agentIsBusy) return;
    const writeIntent =
      workMode === "chapter" && shouldAutoCreateChapterDraft(content);
    let workingChapter = writeIntent
      ? isWritableChapter(activeChapter)
        ? activeChapter
        : [...chapters].reverse().find(isWritableChapter) || null
      : activeChapter;
    if (writeIntent && workingChapter && workingChapter.id !== activeChapter?.id) {
      onChapter(workingChapter);
    }
    if (!workingChapter && writeIntent) {
      setStatus("queued");
      setNotice("正在为这次写作创建一张 Agent 草稿稿纸…");
      try {
        const nextNumber =
          chapters.reduce(
            (maximum, chapter) => Math.max(maximum, chapter.number || 0),
            0,
          ) + 1;
        workingChapter = await createChapter(project.id, {
          chapter_number: nextNumber,
          title: `第${nextNumber}章 · Agent 草稿`,
          status: "draft",
        });
        queryClient.setQueryData<Chapter[]>(
          ["chapters", project.id],
          (current) =>
            [...(current || []), workingChapter as Chapter].sort(
              (left, right) => left.number - right.number,
            ),
        );
        onChapter(workingChapter);
      } catch (error) {
        setStatus("error");
        setNotice(
          error instanceof Error
            ? error.message
            : "Agent 草稿稿纸创建失败，请稍后重试。",
        );
        return;
      }
    }
    if (!workingChapter && workMode === "chapter") {
      setNotice(
        "当前还没有章节；可以切换到全书协作，或先新建一张稿纸。",
      );
      setStatus("idle");
      return;
    }
    if (
      workMode === "global" &&
      proposals.some((proposal) => proposal.status === "proposed")
    ) {
      setNotice("请先在左侧全书 Diff 接受或拒绝本批改动，再继续新的全书任务。");
      onOpenGlobalDiff();
      return;
    }
    setMessage("");
    setNotice("");
    setStreamingText("");
    setLiveOutputStarted(false);
    setStatus("streaming");
    setFailedRunId("");
    beginLiveSend(
      conversation?.id || "",
      sequenceRef.current,
      proposals.map((proposal) => proposal.id),
    );
    const snapshot = contextSnapshot(workingChapter, writeIntent);
    const messageTarget = resolveAgentMessageTarget(
      workMode === "global"
        ? { type: "project", id: project.id, chapter_id: null }
        : target,
      workMode,
      workingChapter,
    );
    const imageId = character?.image_media_id || character?.portrait?.id || "";
    const authorisedAssets = allowImage && imageId ? [imageId] : [];
    setAllowImage(false); // image permission is intentionally one-shot
    const localMessage = {
      id: `local-${Date.now()}`,
      role: "user" as const,
      content,
      created_at: new Date().toISOString(),
      proposal_ids: [],
      target: messageTarget,
      context_snapshot: snapshot,
      authorized_asset_ids: authorisedAssets,
    };
    setMessages((current) => [...current, localMessage]);
    try {
      const active = await ensureConversation();
      const pendingLiveSend = liveSendRef.current;
      if (pendingLiveSend) {
        const sameConversation = pendingLiveSend.conversationId === active.id;
        pendingLiveSend.conversationId = active.id;
        if (!sameConversation) pendingLiveSend.baselineSequence = 0;
      }
      await sendAssistantMessage(project.id, active.id, content, {
        target: messageTarget,
        context_snapshot: snapshot,
        authorized_asset_ids: authorisedAssets,
        expected_version: active.version,
      }).then(({ message: savedMessage, run: startedRun, conversation: updatedConversation }) => {
        activateLiveRun(
          active.id,
          startedRun.id || savedMessage.run_id || "",
        );
        if (updatedConversation) {
          setConversation((current) =>
            current?.id === updatedConversation.id
              ? { ...current, ...updatedConversation, target: current.target }
              : current,
          );
          void queryClient.invalidateQueries({
            queryKey: ["assistant-conversations", project.id],
          });
        }
        setMessages((current) =>
          current.map((item) =>
            item.id === localMessage.id ? savedMessage : item,
          ),
        );
      });
    } catch (error) {
      setStatus("error");
      setNotice(
        error instanceof Error
          ? error.message
          : "Agent 暂时不可用，请检查 Provider 配置。\n",
      );
      liveSendRef.current = null;
      activeRunIdRef.current = "";
    }
  };
  const applyPendingProposals = useCallback(async (proposalIds: string[]) => {
    if (busyProposal) return;
    const selected = proposals.filter(
      (proposal) =>
        proposalIds.includes(proposal.id) && proposal.status === "proposed",
    );
    if (!selected.length) return;
    setBusyProposal("auto-apply");
    try {
      const { proposals: nextRows, memory_run: nextMemoryRun } = await applyAssistantProposals(
        project.id,
        selected.map((proposal) => proposal.id),
        {
          expected_memory_epoch: selected[0].base_memory_epoch,
          expected_versions: Object.fromEntries(
            selected
              .filter((proposal) => proposal.base_version != null)
              .map((proposal) => [
                proposal.id,
                proposal.base_version as number,
              ]),
          ),
        },
      );
      setProposals((current) =>
        current.map((item) => {
          const next = nextRows.find((proposal) => proposal.id === item.id);
          return next
            ? {
                ...item,
                ...next,
                patches: next.patches.length ? next.patches : item.patches,
              }
            : item;
        }),
      );
      nextRows.forEach((proposal) => onProposalDismiss(proposal.id));
      await queryClient.invalidateQueries({
        queryKey: ["project-attention", project.id],
      });
      await onProposalApplied(nextRows);
      if (nextRows.some((proposal) => proposal.target.type === "chapter")) {
        await queryClient.invalidateQueries({
          queryKey: ["chapters", project.id],
        });
      }
      await queryClient.invalidateQueries({
        queryKey: ["project-memory", project.id],
      });
      if (nextMemoryRun) onMemoryRun?.(nextMemoryRun);
      setNotice(
        workMode === "global"
          ? `已接受 ${nextRows.length} 处全书改动，新的全书记忆正在后台整理。`
          : `已自动写入并保存 ${nextRows.length} 处改动。`,
      );
    } catch (error) {
      const code = apiErrorCode(error);
      notifyFallback(
        onNotice,
        "warning",
        code === "proposal_conflict"
          ? "项目内容已在别处变化，这次 Agent 改动未覆盖新内容；请让 Agent 重新生成。"
          : error instanceof Error
            ? error.message
            : "Agent 改动自动写入失败。\n",
      );
    } finally {
      setBusyProposal("");
    }
  }, [
    busyProposal,
    onNotice,
    onProposalApplied,
    onProposalDismiss,
    onMemoryRun,
    project.id,
    proposals,
    queryClient,
    workMode,
  ]);
  const rejectPendingProposals = useCallback(async (proposalIds: string[]) => {
    if (busyProposal) return;
    const selected = proposals.filter(
      (proposal) =>
        proposalIds.includes(proposal.id) && proposal.status === "proposed",
    );
    if (!selected.length) return;
    setBusyProposal("reject-global");
    try {
      const nextRows = await rejectAssistantProposals(
        project.id,
        selected.map((proposal) => proposal.id),
        { reason: "作者拒绝本批全书 Diff" },
      );
      setProposals((current) =>
        current.map((item) =>
          nextRows.find((proposal) => proposal.id === item.id) || item,
        ),
      );
      nextRows.forEach((proposal) => onProposalDismiss(proposal.id));
      setNotice(`已拒绝 ${nextRows.length} 处全书改动，正文保持不变。`);
    } catch (error) {
      notifyFallback(
        onNotice,
        "warning",
        error instanceof Error ? error.message : "全书改动拒绝失败。",
      );
    } finally {
      setBusyProposal("");
    }
  }, [busyProposal, onNotice, onProposalDismiss, project.id, proposals]);
  useEffect(() => {
    const handleGlobalDiffAction = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          projectId?: string;
          action?: "apply" | "reject";
          proposalIds?: string[];
        }>
      ).detail;
      if (detail?.projectId !== project.id || !detail.proposalIds?.length) return;
      if (detail.action === "apply") {
        void applyPendingProposals(detail.proposalIds);
      } else if (detail.action === "reject") {
        void rejectPendingProposals(detail.proposalIds);
      }
    };
    window.addEventListener("story-studio-global-diff-action", handleGlobalDiffAction);
    return () =>
      window.removeEventListener(
        "story-studio-global-diff-action",
        handleGlobalDiffAction,
      );
  }, [applyPendingProposals, project.id, rejectPendingProposals]);
  useEffect(() => {
    if (
      workMode === "global" ||
      busyProposal ||
      status === "queued" ||
      status === "running" ||
      status === "streaming" ||
      proposals.some((proposal) => proposal.status === "building")
    ) {
      return undefined;
    }
    const pending = proposals.filter(
      (proposal) => proposal.status === "proposed",
    );
    if (!pending.length) return undefined;
    const key = pending.map((proposal) => proposal.id).sort().join("|");
    if (autoApplyBatchRef.current === key) return undefined;
    const elapsed = Date.now() - lastProposalActivityRef.current;
    const wait = lastProposalActivityRef.current
      ? Math.max(0, AUTOMATIC_APPLY_PREVIEW_MS - elapsed)
      : 0;
    const timer = window.setTimeout(() => {
      autoApplyBatchRef.current = key;
      void applyPendingProposals(pending.map((proposal) => proposal.id));
    }, wait);
    return () => window.clearTimeout(timer);
  }, [applyPendingProposals, busyProposal, proposals, status, workMode]);
  const retryFailedRun = async () => {
    if (!conversation || !failedRunId || retrying) return;
    setRetrying(true);
    try {
      const retried = await retryAssistantRun(
        project.id,
        conversation.id,
        failedRunId,
      );
      beginLiveSend(
        conversation.id,
        sequenceRef.current,
        proposals.map((proposal) => proposal.id),
      );
      activateLiveRun(conversation.id, retried.id);
      setFailedRunId("");
      setLiveOutputStarted(false);
      setStatus("queued");
      setNotice("已从上次中断处继续，正在重新连接 Agent…");
      await queryClient.invalidateQueries({
        queryKey: ["project-attention", project.id],
      });
    } catch (error) {
      setStatus("error");
      setNotice(
        error instanceof Error ? error.message : "重试没有启动，请稍后再试。",
      );
    } finally {
      setRetrying(false);
    }
  };
  const stopActiveRun = async () => {
    if (stopping) return;
    setStopping(true);
    try {
      let activeConversation = conversationRef.current || conversation;
      let runId = currentRunId || activeRunIdRef.current;
      for (
        let attempt = 0;
        attempt < 40 && (!activeConversation || !runId);
        attempt += 1
      ) {
        await new Promise((resolve) => window.setTimeout(resolve, 50));
        activeConversation = conversationRef.current;
        runId = activeRunIdRef.current;
      }
      if (!activeConversation || !runId) {
        throw new Error("任务仍在建立连接，请稍后再试。");
      }
      await cancelAssistantRun(project.id, activeConversation.id, runId);
      activeRunIdRef.current = "";
      setCurrentRunId("");
      liveSendRef.current = null;
      streamingMessageIdRef.current = "";
      streamingTextRef.current = "";
      setStreamingText("");
      setLiveOutputStarted(false);
      setStatus("cancelled");
      setActiveStage("");
      setNotice("已停止本次任务，已经写出的内容仍留在对话中。你可以继续补充新指令。");
      await queryClient.invalidateQueries({
        queryKey: ["assistant-conversations", project.id],
      });
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "任务没有停止，请稍后重试。",
      );
    } finally {
      setStopping(false);
    }
  };
  const changeWorkMode = (nextMode: AgentWorkMode) => {
    if (nextMode === workMode) return;
    const latest = conversationRows.find(
      (row) => conversationWorkMode(row.purpose) === nextMode,
    );
    onWorkModeChange(nextMode);
    if (latest) {
      void loadConversation(latest.id);
    } else {
      startNewConversation(nextMode);
    }
  };
  const hasConversationProvider = Boolean(conversation?.id);
  const effectiveProviderName = hasConversationProvider
    ? conversation?.provider_name || "当前会话连接"
    : assistantProvider?.name || "尚未添加模型";
  const canSeeImage = hasConversationProvider
    ? conversation?.provider_capabilities?.vision === true
    : assistantProvider?.capabilities?.vision === true;
  const targetLabel =
    workMode === "global"
      ? project.title
      : target.type === "character"
      ? character?.name || "未命名人物"
      : target.type === "thread"
        ? "当前情节线"
        : target.type === "relationship"
          ? "当前关系"
          : activeChapter?.title || "当前稿纸";
  const targetScopeLabel =
    workMode === "global"
      ? "全书协作"
      : target.type === "character"
      ? "人物设定"
      : target.type === "thread"
        ? "情节线"
        : target.type === "relationship"
          ? "人物关系"
          : "当前章节";
  const globalPending =
    workMode === "global"
      ? proposals.filter((proposal) => proposal.status === "proposed")
      : [];
  const quickPromptVisibility = getAgentQuickPromptVisibility(
    target,
    relationNodeCount,
  );
  const reduceMotion = useReducedMotion();
  const showThinkingTransition =
    !liveOutputStarted &&
    (status === "queued" || status === "running" || status === "streaming");
  const thinkingTitle =
    status === "queued" ? "Agent 正在准备这次协作" : "Agent 正在思考";
  const thinkingDetail =
    activeStage === "extracting_proposals"
      ? "正在把回复整理成人物卡、正文或图谱草稿"
      : activeStage === "streaming"
        ? `正在起草关于${targetScopeLabel}的回应`
        : status === "queued"
          ? "正在恢复本轮上下文与较早对话记忆"
          : `正在梳理${targetScopeLabel}与当前上下文`;
  const hasPreviewProposal = proposals.some(isPreviewProposal);
  const agentRailTone =
    status === "error"
      ? "error"
      : hasPreviewProposal
          ? "change"
        : status === "reconnecting" || status === "disconnected"
          ? "warning"
          : status === "queued" ||
                status === "running" ||
                status === "streaming"
              ? "busy"
              : "idle";
  const agentRailText =
    (status === "error" || status === "cancelled") && notice
      ? notice
      : hasPreviewProposal
      ? workMode === "global"
        ? "全书改动已进入左侧 Diff"
        : "改动已显示在当前内容"
      : notice ||
        (status === "reconnecting"
          ? "正在恢复实时连接，已收到的内容不会丢失…"
          : status === "disconnected"
            ? "实时连接暂时中断，正在等待恢复。"
            : status === "queued" ||
                status === "running" ||
                status === "streaming"
              ? "Agent 正在处理当前请求…"
              : "Agent 已就位，等待你的下一条指令。");
  const AgentRailIcon =
    agentRailTone === "error" || agentRailTone === "warning"
      ? CircleAlert
      : agentRailTone === "change"
        ? CheckCircle2
        : agentRailTone === "busy"
          ? Loader2
          : Bot;
  return (
    <aside
      className={`agent-dock ${mobileVisible ? "" : "mobile-panel-hidden"}`}
    >
      <div className="agent-dock-head">
        <div className="agent-dock-identity">
          <span className="agent-dock-mark" aria-hidden="true">
            <Bot size={17} />
          </span>
          <div>
            <span className="agent-dock-kicker">持续协作</span>
            <h2>和 Agent 一起写</h2>
          </div>
        </div>
        <div className="agent-dock-head-actions">
          <span
            className={`agent-status-dot agent-status-${status}`}
            title={status}
          />
          <button
            type="button"
            className="agent-head-action"
            onClick={() => setHistoryOpen((open) => !open)}
            aria-label={historyOpen ? "收起历史对话" : "查看历史对话"}
            aria-expanded={historyOpen}
            title="历史对话"
          >
            <Clock3 size={14} />
            {conversationRows.length > 0 && <small>{conversationRows.length}</small>}
          </button>
          <button
            type="button"
            className="agent-head-action"
            onClick={() => startNewConversation()}
            aria-label="新建 Agent 对话"
            title="新对话"
          >
            <Plus size={15} />
          </button>
        </div>
      </div>
      <div className="agent-session-meta" aria-label="当前 Agent 会话信息">
        <small className="agent-provider-state">
          <span className={`status-dot ${canSeeImage ? "green" : ""}`} />
          {effectiveProviderName}
          {canSeeImage ? " · 可看图" : ""}
        </small>
        <span aria-label={`已记住 ${rememberedTurns} 轮对话`}>
          {rememberedTurns ? `记忆 ${rememberedTurns} 轮` : "新对话"}
        </span>
      </div>
      <div
        className={`agent-status-rail agent-status-rail-${agentRailTone}`}
        aria-label="Agent 当前状态"
      >
        <AnimatePresence initial={false} mode="wait">
          <motion.div
            className="agent-notice"
            key={`${agentRailTone}:${agentRailText}`}
            role="status"
            aria-live={agentRailTone === "error" ? "assertive" : "polite"}
            aria-atomic="true"
            initial={reduceMotion ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.14 }}
          >
            <AgentRailIcon
              size={13}
              className={agentRailTone === "busy" ? "spin" : undefined}
            />
            <span>{agentRailText}</span>
            {failedRunId && (
              <button
                type="button"
                className="button button-secondary button-small"
                onClick={() => void retryFailedRun()}
                disabled={retrying}
              >
                <RefreshCw size={12} /> {retrying ? "正在重试…" : "继续重试"}
              </button>
            )}
          </motion.div>
        </AnimatePresence>
      </div>
      <AnimatePresence initial={false}>
        {historyOpen && (
          <motion.section
            className="agent-history-panel"
            role="dialog"
            aria-label="历史 Agent 会话"
            initial={reduceMotion ? false : { opacity: 0, x: 18 }}
            animate={{ opacity: 1, x: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, x: 18 }}
            transition={{ duration: reduceMotion ? 0 : 0.18 }}
          >
            <header>
              <div>
                <span>协作档案</span>
                <strong>历史对话</strong>
              </div>
              <button
                type="button"
                className="agent-head-action"
                onClick={() => setHistoryOpen(false)}
                aria-label="关闭历史对话"
              >
                <X size={15} />
              </button>
            </header>
            <label className="agent-history-search">
              <Search size={13} />
              <input
                value={historySearch}
                onChange={(event) => setHistorySearch(event.target.value)}
                placeholder="搜索对话名称"
                aria-label="搜索历史对话"
                autoFocus
              />
            </label>
            <div className="agent-history-modes" role="group" aria-label="筛选历史对话">
              {([
                ["all", "全部"],
                ["global", "全书协作"],
                ["chapter", "当前章节"],
              ] as const).map(([value, label]) => (
                <button
                  type="button"
                  key={value}
                  className={historyMode === value ? "is-active" : ""}
                  onClick={() => setHistoryMode(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="agent-history-new"
              onClick={() => startNewConversation()}
            >
              <Plus size={14} /> 新建{workMode === "global" ? "全书" : "章节"}对话
            </button>
            <div
              className="agent-history-list"
              role="listbox"
              aria-label="历史对话列表"
            >
              {filteredConversationRows.length ? (
                filteredConversationRows.map((row) => {
                  const selected = row.id === conversation?.id;
                  return (
                    <button
                      type="button"
                      role="option"
                      aria-selected={selected}
                      key={row.id}
                      onClick={() => {
                        setHistoryOpen(false);
                        setHistorySearch("");
                        void loadConversation(row.id);
                      }}
                    >
                      <span className="agent-history-glyph" aria-hidden="true">
                        {selected ? <Bot size={13} /> : <MessageCircle size={13} />}
                      </span>
                      <span className="agent-history-copy">
                        <strong>{row.title || "新的写作对话"}</strong>
                        <small>
                          {conversationWorkMode(row.purpose) === "global"
                            ? "全书协作"
                            : "当前章节"}
                          {selected ? " · 当前对话" : ""}
                          {row.updated_at ? ` · ${formatDate(row.updated_at)}` : ""}
                        </small>
                      </span>
                      {selected && <i>正在使用</i>}
                    </button>
                  );
                })
              ) : (
                <div className="agent-history-empty">
                  <MessageCircle size={18} />
                  <strong>{historySearch ? "没有匹配的对话" : "还没有历史对话"}</strong>
                  <small>{historySearch ? "换一个关键词试试" : "发送第一条消息后会自动保存在这里"}</small>
                </div>
              )}
            </div>
          </motion.section>
        )}
      </AnimatePresence>
      <section className="agent-scope agent-context" aria-label="Agent 工作方式">
        <div
          className="agent-scope-tabs"
          role="group"
          aria-label="Agent 工作方式选择"
        >
          <button
            type="button"
            className={workMode === "global" ? "is-active" : ""}
            onClick={() => changeWorkMode("global")}
          >
            <BookOpenCheck size={12} /> 全书协作
          </button>
          <button
            type="button"
            className={workMode === "chapter" ? "is-active" : ""}
            onClick={() => changeWorkMode("chapter")}
            disabled={!activeChapter}
          >
            <FileText size={12} /> 当前章节
          </button>
        </div>
        <div className="agent-context-ledger">
          <PencilLine size={13} />
          <span>
            <strong>{targetLabel}</strong>
            <small>
              {workMode === "global"
                ? "稳定版全书记忆 + 相关章节检索；改动先进入 Diff"
                : `自动跟随当前稿纸 · 全书摘要 + 前 10 章`}
            </small>
          </span>
          {globalPending.length > 0 && (
            <button type="button" onClick={onOpenGlobalDiff}>
              查看 {globalPending.length} 处 Diff
            </button>
          )}
        </div>
      </section>
      {character?.portrait && (
        <div className="agent-image-auth">
          <label>
            <input
              type="checkbox"
              checked={allowImage}
              onChange={(event) => setAllowImage(event.target.checked)}
              disabled={!canSeeImage}
            />{" "}
            让 Agent 看这张人物图（本次）
          </label>
          <small>
            {canSeeImage
              ? `${effectiveProviderName} 可以看图；发送后授权立即清空。`
              : `${effectiveProviderName} 没有开启视觉输入，本次不能发送图片。`}
          </small>
        </div>
      )}
      <div className="agent-transcript" aria-live="polite">
        <div className="agent-messages">
        {messages.length === 0 && !streamingText ? (
          <div className="agent-empty">
            <MessageCircle size={18} />
            <strong>把脑海里的片段说出来</strong>
            <p>
              例如：“她表面冷静，其实害怕再次失去家人。” Agent
              会把它拆成可以编辑的字段。
            </p>
            {(quickPromptVisibility.motivation ||
              quickPromptVisibility.tension) && (
              <div className="agent-quick-prompts">
                {quickPromptVisibility.motivation && (
                  <button
                    type="button"
                    onClick={() =>
                      setMessage("帮我补齐这个人物的动机、目标和核心冲突")
                    }
                  >
                    补齐人物动力
                  </button>
                )}
                {quickPromptVisibility.tension && (
                  <button
                    type="button"
                    onClick={() =>
                      setMessage("根据现有设定，提出三种更有张力的关系")
                    }
                  >
                    增加关系张力
                  </button>
                )}
              </div>
            )}
          </div>
        ) : (
          <>
            {messages.map((item) => {
              const content =
                item.role === "assistant"
                  ? assistantMessageText(item.content)
                  : item.content;
              if (!content) return null;
              return item.status === "partial" ? (
                <details
                  className="agent-message agent-message-partial"
                  key={item.id}
                >
                  <summary>上次未完成</summary>
                  <p>{content}</p>
                </details>
              ) : (
                <div
                  className={`agent-message agent-message-${item.role}`}
                  key={item.id}
                >
                  <span>
                    {item.role === "user"
                      ? "你"
                      : item.role === "assistant"
                        ? "Agent"
                        : "系统"}
                  </span>
                  <p>{content}</p>
                  {item.context_snapshot?.selection && (
                    <small className="agent-message-context">
                      选区 {item.context_snapshot.selection.start}–
                      {item.context_snapshot.selection.end}
                    </small>
                  )}
                </div>
              );
            })}
            <AnimatePresence initial={false}>
              {showThinkingTransition && (
                <motion.div
                  key="agent-thinking"
                  className="agent-thinking"
                  role="status"
                  aria-label={thinkingTitle}
                  initial={reduceMotion ? false : { opacity: 0, y: 5 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -4 }}
                  transition={{ duration: reduceMotion ? 0 : 0.2 }}
                >
                  <span className="agent-thinking-mark" aria-hidden="true">
                    <PencilLine size={13} />
                  </span>
                  <span className="agent-thinking-copy">
                    <strong>{thinkingTitle}</strong>
                    <small>{thinkingDetail}</small>
                  </span>
                  <span className="agent-thinking-ink" aria-hidden="true">
                    <span className="agent-thinking-dots">
                      <i />
                      <i />
                      <i />
                    </span>
                    <span className="agent-thinking-stroke" />
                  </span>
                </motion.div>
              )}
            </AnimatePresence>
            {streamingText &&
              (!streamingMessageIdRef.current ||
                !messages.some(
                  (item) => item.id === streamingMessageIdRef.current,
                )) && (
              <div className="agent-message agent-message-assistant agent-message-live">
                <span>Agent</span>
                <p>
                  {visibleAgentMessage(streamingText)}
                  <i className="agent-caret" />
                </p>
              </div>
            )}
          </>
        )}
      </div>
      </div>
      <div className="agent-compose">
        <div className="agent-compose-context">
          <span>{targetScopeLabel}</span>
          <strong>{targetLabel}</strong>
        </div>
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void send();
            }
          }}
          placeholder={
            globalPending.length
              ? "请先处理左侧全书 Diff，再继续本次协作"
              : workMode === "global"
                ? "描述需要贯穿全书检查或修改的内容…"
                : "告诉 Agent 当前章节需要怎样继续或修改…"
          }
          rows={3}
          aria-label="发送给 Agent 的消息"
          disabled={globalPending.length > 0}
        />
        <div className="agent-compose-actions">
          <small>⌘ / Ctrl + Enter 发送</small>
          {agentIsBusy ? (
            <button
              type="button"
              className="button agent-stop-button button-small"
              onClick={() => void stopActiveRun()}
              disabled={stopping}
              aria-label="停止 Agent 当前任务"
            >
              <Square size={11} fill="currentColor" />
              {stopping ? "正在停止…" : "停止"}
            </button>
          ) : (
            <button
              type="button"
              className="button button-primary button-small"
              onClick={() => void send()}
              disabled={!message.trim() || globalPending.length > 0}
            >
              <Send size={13} /> 发送
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}

export default function StoryStudio({
  project,
  storyMap,
  chapters,
  activeChapter,
  activeContent,
  assistantProvider,
  memoryRun,
  projectMemory,
  initialMode = "characters",
  autoOpenAgent = false,
  onContentChange,
  onCreateChapter,
  onImport,
  onAnalyzeMemory,
  onRetryMemory,
  onMemoryRun,
  onModeChange,
  onChapter,
  onNotice,
}: StoryStudioProps) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<StudioMode>(initialMode);
  const [agentWorkMode, setAgentWorkMode] = useState<AgentWorkMode>("chapter");
  const [entityView, setEntityView] = useState<EntityViewMode>(
    initialMode === "story-map" ? "graph" : "table",
  );
  const [selectedCharacterId, setSelectedCharacterId] = useState<string | null>(
    null,
  );
  const [editing, setEditing] = useState<CharacterCard | null>(null);
  const [isNewCharacter, setIsNewCharacter] = useState(false);
  const [expandedCharacter, setExpandedCharacter] =
    useState<CharacterCard | null>(null);
  const [manualPaths, setManualPaths] = useState<Set<string>>(new Set());
  const [previewProposals, setPreviewProposals] = useState<
    Record<string, AssistantProposal>
  >({});
  const [graphTarget, setGraphTarget] = useState<AgentTarget | null>(null);
  const [pendingPortrait, setPendingPortrait] = useState<File | null>(null);
  const [pendingPortraitUrl, setPendingPortraitUrl] = useState("");
  const changeMode = (next: StudioMode) => {
    setMode(next);
    if (next !== "story-map") setGraphTarget(null);
    onModeChange(next);
  };
  useEffect(() => {
    setMode(initialMode);
    setEntityView(initialMode === "story-map" ? "graph" : "table");
  }, [initialMode, project.id]);
  useEffect(() => {
    // Project changes are the reset boundary. Internal Agent-driven surface
    // changes also update `initialMode` in the shell, but must preserve the
    // draft person/edge that the user just asked to reveal.
    setIsNewCharacter(false);
    setSelectedCharacterId(null);
    setGraphTarget(null);
    setAgentWorkMode("chapter");
  }, [project.id]);
  const charactersQuery = useQuery({
    queryKey: ["characters", project.id],
    queryFn: () => getCharacters(project.id),
  });
  const graphQuery = useQuery({
    queryKey: ["story-graph", project.id, activeChapter?.id],
    queryFn: () => getStoryGraph(project.id, activeChapter?.id),
    enabled: Boolean(activeChapter),
    retry: false,
  });
  const characters = charactersQuery.data || [];
  const selectedCharacter =
    characters.find((character) => character.id === selectedCharacterId) ||
    null;
  useEffect(() => {
    if (!selectedCharacterId && characters[0])
      setSelectedCharacterId(characters[0].id);
    if (
      selectedCharacterId &&
      !characters.some((character) => character.id === selectedCharacterId) &&
      !isNewCharacter
    )
      setSelectedCharacterId(characters[0]?.id || null);
  }, [characters, isNewCharacter, selectedCharacterId]);
  useEffect(() => {
    if (!isNewCharacter) {
      setEditing(
        selectedCharacter
          ? {
              ...selectedCharacter,
              aliases: [...selectedCharacter.aliases],
              tags: [...selectedCharacter.tags],
              custom_fields: { ...selectedCharacter.custom_fields },
            }
          : null,
      );
      setManualPaths(new Set());
    }
  }, [isNewCharacter, selectedCharacter]);
  useEffect(
    () => () => {
      if (pendingPortraitUrl) URL.revokeObjectURL(pendingPortraitUrl);
    },
    [pendingPortraitUrl],
  );
  useEffect(() => {
    const onMobilePanel = (event: Event) => {
      const panel = (
        event as CustomEvent<"agent" | "content" | "dossier" | "graph">
      ).detail;
      if (panel === "graph") {
        changeMode("story-map");
        setEntityView("graph");
      } else if (panel === "content" || panel === "dossier") {
        // Content returns to whichever writing/people/graph surface was open;
        // the mobile tab should not silently discard that context.
        return;
      } else if (panel === "agent") {
        return;
      } else if (mode === "story-map") {
        changeMode("characters");
        setEntityView("table");
      }
    };
    window.addEventListener("story-studio-mobile-panel", onMobilePanel);
    return () =>
      window.removeEventListener("story-studio-mobile-panel", onMobilePanel);
  }, [mode]);
  const saveCharacterMutation = useMutation({
    mutationFn: async () => {
      if (!editing) throw new Error("还没有选择人物");
      const saved = isNewCharacter
        ? await createCharacter(project.id, characterPayload(editing))
        : await updateCharacter(
            project.id,
            editing.id,
            characterPayload(editing),
          );
      if (pendingPortrait) {
        const portrait = await uploadCharacterPortrait(
          project.id,
          saved.id,
          pendingPortrait,
          `${saved.name}的人物肖像`,
        );
        const refreshed = await getCharacter(saved.id);
        return { ...refreshed, portrait, image_media_id: portrait.id };
      }
      return saved;
    },
    onSuccess: (saved) => {
      queryClient.setQueryData<CharacterCard[]>(
        ["characters", project.id],
        (current) => {
          const list = current || [];
          return isNewCharacter
            ? [...list, saved]
            : list.map((item) => (item.id === saved.id ? saved : item));
        },
      );
      setSelectedCharacterId(saved.id);
      setEditing({
        ...saved,
        aliases: [...saved.aliases],
        tags: [...saved.tags],
        custom_fields: { ...saved.custom_fields },
      });
      setIsNewCharacter(false);
      setPendingPortrait(null);
      if (pendingPortraitUrl) URL.revokeObjectURL(pendingPortraitUrl);
      setPendingPortraitUrl("");
      setManualPaths(new Set());
      notifyFallback(onNotice, "success", "人物卷宗已保存，当前设定已生效。\n");
    },
    onError: (error) =>
      notifyFallback(
        onNotice,
        "error",
        error instanceof Error ? error.message : "人物卷宗保存失败。\n",
      ),
  });
  const refreshProjectAfterProposal = useCallback(
    async (input?: AssistantProposal | AssistantProposal[]) => {
      const appliedProposals = Array.isArray(input)
        ? input
        : input
          ? [input]
          : [];
      try {
        const [
          latestProjects,
          latestCharacters,
          latestChapters,
          latestStoryMap,
          latestGraph,
        ] = await Promise.all([
          getProjects(),
          getCharacters(project.id),
          getChapters(project.id),
          getStoryMap(project.id),
          getStoryGraph(project.id, activeChapter?.id),
        ]);
        queryClient.setQueryData(["projects"], latestProjects);
        queryClient.setQueryData(["characters", project.id], latestCharacters);
        queryClient.setQueryData(["chapters", project.id], latestChapters);
        queryClient.setQueryData(["story-map", project.id], latestStoryMap);
        queryClient.setQueryData(
          ["story-graph", project.id, activeChapter?.id],
          latestGraph,
        );
        const targetProposal =
          appliedProposals.find(
            (proposal) => proposal.target.type === "character",
          ) || appliedProposals[0];
        const targetId = targetProposal?.target_id || targetProposal?.target.id;
        const current =
          editing && editing.id !== "new-character"
            ? latestCharacters.find((item) => item.id === editing.id)
            : targetId && targetProposal?.target.type === "character"
              ? latestCharacters.find((item) => item.id === targetId)
              : undefined;
        if (current) {
          setSelectedCharacterId(current.id);
          setEditing({
            ...current,
            aliases: [...current.aliases],
            tags: [...current.tags],
            custom_fields: { ...current.custom_fields },
          });
          setIsNewCharacter(false);
        }
      } catch (error) {
        notifyFallback(
          onNotice,
          "warning",
          error instanceof Error
            ? error.message
            : "提案已应用，但项目资料刷新失败，请稍后重试。",
        );
      }
    },
    [activeChapter?.id, editing, onNotice, project.id, queryClient],
  );
  const handleProposalPreview = useCallback((proposal: AssistantProposal) => {
    setPreviewProposals((current) => ({ ...current, [proposal.id]: proposal }));
  }, []);
  const dismissProposalPreview = useCallback((proposalId: string) => {
    setPreviewProposals((current) => {
      if (!current[proposalId]) return current;
      const next = { ...current };
      delete next[proposalId];
      return next;
    });
  }, []);
  const followAgentProposal = (proposal: AssistantProposal) => {
    if (agentWorkMode === "global") {
      changeMode("global-diff");
      notifyFallback(
        onNotice,
        "info",
        "Agent 正按章节整理全书改动；左侧 Diff 会持续更新。",
      );
      return;
    }
    const operation = String(proposal.operation || "").toLowerCase();
    const targetType = String(
      proposal.target_type || proposal.target.type || "",
    ).toLowerCase();
    const patch = chapterPatchValues(proposal);
    const chapterId = String(
      (targetType.includes("chapter") ? proposal.target_id || proposal.target.id : "") ||
        patch.chapter_id ||
        patch.chapterId ||
        "",
    );
    if (targetType.includes("chapter") || operation.includes("chapter")) {
      const chapter = chapters.find((item) => item.id === chapterId);
      if (chapter) onChapter(chapter);
      changeMode("manuscript");
      notifyFallback(
        onNotice,
        "info",
        chapter
          ? `Agent 正文改动已显示在《${chapter.title}》标题旁。`
          : "正文改动已生成，但对应稿纸暂未找到。",
      );
      return;
    }
    if (
      targetType.includes("graph") ||
      targetType.includes("thread") ||
      targetType.includes("relation") ||
      targetType.includes("relationship") ||
      operation.includes("graph")
    ) {
      changeMode("story-map");
      setEntityView("graph");
      setGraphTarget(proposal.target);
      notifyFallback(onNotice, "info", "Agent 正在图谱中生成节点和连线，完成后会自动保存。");
      return;
    }
    if (targetType.includes("character") || operation.includes("character")) {
      changeMode("characters");
      setEntityView("table");
      const targetId = proposal.target_id || proposal.target.id;
      const current = targetId
        ? characters.find((item) => item.id === targetId)
        : undefined;
      setManualPaths(new Set());
      if (current) {
        setIsNewCharacter(false);
        setSelectedCharacterId(current.id);
        setEditing({
          ...current,
          aliases: [...current.aliases],
          tags: [...current.tags],
          custom_fields: { ...current.custom_fields },
        });
      } else {
        setIsNewCharacter(true);
        setSelectedCharacterId("new-character");
        // Keep the form's canonical new-character identity. Agent values are
        // rendered as a non-persistent diff beside each field.
        setEditing(emptyCharacter(project.id));
      }
      notifyFallback(onNotice, "info", "Agent 正在人物卷宗中逐项填写，完成后会自动保存。");
    }
  };
  const handleManualCharacterChange = useCallback(
    (next: CharacterCard, path?: string) => {
      setEditing(next);
      if (path) setManualPaths((current) => new Set(current).add(path));
    },
    [],
  );
  const handleUpload = (file: File) => {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      notifyFallback(onNotice, "warning", "请选择 JPG、PNG 或 WebP 图片。\n");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      notifyFallback(onNotice, "warning", "图片不能超过 10 MB。\n");
      return;
    }
    if (!editing) return;
    const url = URL.createObjectURL(file);
    if (pendingPortraitUrl) URL.revokeObjectURL(pendingPortraitUrl);
    setPendingPortrait(file);
    setPendingPortraitUrl(url);
    setEditing({
      ...editing,
      portrait: {
        id: "local-preview",
        project_id: project.id,
        url,
        filename: file.name,
        alt: `${editing.name || "人物"}人物肖像`,
      },
    });
  };
  const removePortrait = async () => {
    if (!editing) return;
    if (pendingPortraitUrl) URL.revokeObjectURL(pendingPortraitUrl);
    setPendingPortrait(null);
    setPendingPortraitUrl("");
    if (
      editing.id !== "new-character" &&
      editing.portrait?.id &&
      editing.portrait.id !== "local-preview"
    ) {
      try {
        await deleteCharacterPortrait(
          project.id,
          editing.id,
          editing.image_media_id || editing.portrait.id,
        );
        const refreshed = await getCharacter(editing.id);
        queryClient.setQueryData<CharacterCard[]>(
          ["characters", project.id],
          (current) =>
            (current || []).map((item) =>
              item.id === refreshed.id ? refreshed : item,
            ),
        );
        setEditing(refreshed);
        return;
      } catch (error) {
        notifyFallback(
          onNotice,
          "warning",
          error instanceof Error ? error.message : "头像移除失败。\n",
        );
      }
    }
    setEditing({ ...editing, portrait: null });
  };
  const newCharacter = () => {
    changeMode("characters");
    setIsNewCharacter(true);
    setSelectedCharacterId("new-character");
    setEditing(emptyCharacter(project.id));
    setManualPaths(new Set());
  };
  const storyGraphFallback = useMemo(
    () => fallbackGraph(project, characters, storyMap, activeChapter),
    [activeChapter, characters, project, storyMap],
  );
  const persistedGraph = graphQuery.data ||
    storyMap.graph || { nodes: [], edges: [] };
  const graph = useMemo(
    () => mergeStoryGraphs(persistedGraph, storyGraphFallback),
    [persistedGraph, storyGraphFallback],
  );
  const liveProposals = useMemo(
    () =>
      Object.values(previewProposals).filter(
        (proposal) =>
          !proposal.scope_chapter_id ||
          proposal.scope_chapter_id === activeChapter?.id,
      ),
    [activeChapter?.id, previewProposals],
  );
  const visibleGraph = useMemo(
    () => graphWithAgentDrafts(graph, liveProposals),
    [graph, liveProposals],
  );
  const agentBuild = useMemo(
    () => summarizeAgentBuild(liveProposals, visibleGraph),
    [liveProposals, visibleGraph],
  );
  const draftCharacters = useMemo(
    () =>
      liveProposals
        .map((proposal) => {
          const character = proposalDraftCharacter(project.id, proposal);
          return character ? { proposal, character } : null;
        })
        .filter((item): item is AgentCharacterDraft => Boolean(item)),
    [liveProposals, project.id],
  );
  const liveCharacterCount = characters.length + draftCharacters.length;
  const liveGraphCount = visibleGraph.nodes.length + visibleGraph.edges.length;
  const globalDiffProposals = useMemo(
    () => {
      const chapterOrder = new Map(
        chapters.map((chapter) => [chapter.id, chapter.number]),
      );
      return Object.values(previewProposals)
        .filter(isPreviewProposal)
        .sort((left, right) => {
          const chapterId = (proposal: AssistantProposal) =>
            String(
              proposal.target_id ||
                proposal.scope_chapter_id ||
                proposal.target.chapter_id ||
                proposal.target.id ||
                "",
            );
          const leftOrder = chapterOrder.get(chapterId(left)) ?? Number.MAX_SAFE_INTEGER;
          const rightOrder = chapterOrder.get(chapterId(right)) ?? Number.MAX_SAFE_INTEGER;
          return leftOrder - rightOrder;
        });
    },
    [chapters, previewProposals],
  );
  const manuscriptAgentDraft = useMemo(
    () => chapterAgentDraft(activeChapter, activeContent, liveProposals),
    [activeChapter, activeContent, liveProposals],
  );
  const agentDrafts = useMemo(
    () => characterAgentDrafts(editing, liveProposals, manualPaths),
    [editing, liveProposals, manualPaths],
  );
  const target = useMemo<AgentTarget>(
    () =>
      mode === "characters" && editing
        ? { type: "character", id: editing.id }
        : mode === "story-map" && graphTarget
          ? graphTarget
        : { type: "project", id: project.id },
    [editing?.id, graphTarget, mode, project.id],
  );
  return (
    <LayoutGroup>
      <div className="studio-page">
        <div className="studio-layout">
          <aside className="studio-sidebar">
            <div className="studio-nav-label">
              <span>工作区</span>
              <small>{chapters.length} 张稿纸</small>
            </div>
            <nav className="studio-nav" aria-label="工作区分类">
              <button
                className={mode === "global-diff" ? "is-active" : ""}
                onClick={() => changeMode("global-diff")}
                title="全书 Diff"
              >
                <FileDiff size={15} /> 全书 Diff{" "}
                <small className={globalDiffProposals.length ? "has-pending" : ""}>
                  {globalDiffProposals.length}
                </small>
              </button>
              <button
                className={mode === "manuscript" ? "is-active" : ""}
                onClick={() => changeMode("manuscript")}
                title="写作"
              >
                <FileText size={15} /> 写作
              </button>
              <button
                className={mode === "characters" ? "is-active" : ""}
                onClick={() => changeMode("characters")}
                title="人物"
              >
                <UserRound size={15} /> 人物{" "}
                <small>{liveCharacterCount}</small>
              </button>
              <button
                className={mode === "story-map" ? "is-active" : ""}
                onClick={() => {
                  changeMode("story-map");
                  setEntityView("graph");
                }}
                title="故事图谱"
              >
                <Network size={15} /> 故事图谱 <small>{liveGraphCount}</small>
              </button>
              <button
                className={mode === "memory" ? "is-active" : ""}
                onClick={() => changeMode("memory")}
                title="全书记忆"
              >
                <BookOpenCheck size={15} /> 全书记忆
                {memoryRun && ["queued", "running"].includes(memoryRun.status) ? (
                  <small>{Math.round(memoryRun.progress || 0)}%</small>
                ) : null}
              </button>
            </nav>
            {globalDiffProposals.length > 0 && (
              <section className="studio-diff-index" aria-label="全书 Diff 章节索引">
                <header>
                  <span>全书校样</span>
                  <small>{globalDiffProposals.length} 处</small>
                </header>
                {globalDiffProposals.map((proposal, index) => {
                  const chapterId = String(
                    proposal.target_id ||
                      proposal.scope_chapter_id ||
                      proposal.target.chapter_id ||
                      proposal.target.id ||
                      "",
                  );
                  const changedChapter = chapters.find((item) => item.id === chapterId);
                  return (
                    <button
                      type="button"
                      key={proposal.id}
                      onClick={() => {
                        changeMode("global-diff");
                        window.requestAnimationFrame(() =>
                          document
                            .getElementById(`global-diff-${proposal.id}`)
                            ?.scrollIntoView({ behavior: "smooth", block: "start" }),
                        );
                      }}
                    >
                      <b>{String(index + 1).padStart(2, "0")}</b>
                      <span>
                        <strong>
                          {changedChapter
                            ? `第 ${changedChapter.number} 章 · ${changedChapter.title}`
                            : "全书资料"}
                        </strong>
                        <small>
                          {proposal.status === "building" ? "正在修改" : "等待整批确认"}
                        </small>
                      </span>
                    </button>
                  );
                })}
              </section>
            )}
            <StudioChapterList
              chapters={chapters}
              activeChapterId={activeChapter?.id}
              onChapter={(chapter) => {
                changeMode("manuscript");
                onChapter(chapter);
              }}
            />
            {mode === "characters" && (
              <div className="studio-character-list">
                <div className="studio-character-list-head">
                  <span>人物索引</span>
                  <button
                    className="quiet-icon"
                    onClick={newCharacter}
                    aria-label="新增人物"
                  >
                    <Plus size={15} />
                  </button>
                </div>
                {characters.map((character) => (
                  <button
                    className={`studio-character-list-item ${character.id === selectedCharacterId ? "is-selected" : ""}`}
                    key={character.id}
                    onClick={() => {
                      setIsNewCharacter(false);
                      setSelectedCharacterId(character.id);
                    }}
                  >
                    <CharacterPortrait character={character} />
                    <span>
                      <strong>{character.name || "未命名人物"}</strong>
                      <small>{character.role || "待补身份"}</small>
                    </span>
                    <span className={`mini-status status-${character.status}`}>
                      {character.status === "confirmed" || character.status === "active"
                        ? "已生效"
                        : character.status === "needs_review"
                          ? "需整理"
                          : "草稿"}
                    </span>
                  </button>
                ))}
                {draftCharacters.length > 0 && (
                  <div className="studio-list-agent-drafts">
                    <Sparkles size={13} /> {draftCharacters.length} 张人物草稿生成中
                  </div>
                )}
                {liveCharacterCount === 0 && (
                  <div className="studio-list-empty">
                    <UserRound size={16} />
                    还没有人物
                    <br />
                    <small>可手动新增，或请 Agent 先搭一张卷宗。</small>
                  </div>
                )}
              </div>
            )}
          </aside>
          <main className="studio-main">
            {memoryRun && ["queued", "running"].includes(memoryRun.status) && (
              <button
                type="button"
                className="memory-progress-notice"
                onClick={() => changeMode("memory")}
              >
                <span className="memory-progress-seal" aria-hidden="true">
                  <BookOpenCheck size={15} />
                </span>
                <span>
                  <small>全书记忆正在后台整理</small>
                  <strong>{memoryRun.phase_label || "收集已确认正文与最新变更"}</strong>
                  <i
                    role="progressbar"
                    aria-label="全书记忆整理进度"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(memoryRun.progress || 0)}
                  >
                    <b style={{ width: `${Math.max(3, memoryRun.progress || 0)}%` }} />
                  </i>
                </span>
                <em>{Math.round(memoryRun.progress || 0)}%</em>
              </button>
            )}
            <AgentLiveBuildRail
              summary={agentBuild}
              workMode={agentWorkMode}
              activeSurface={
                mode === "story-map" ||
                (mode === "characters" && entityView === "graph")
                  ? "graph"
                  : mode === "characters"
                    ? "characters"
                    : null
              }
              onShowCharacters={() => {
                changeMode("characters");
                setEntityView("table");
              }}
              onShowGraph={() => {
                changeMode("story-map");
                setEntityView("graph");
              }}
            />
            {mode === "global-diff" ? (
              <GlobalDiffWorkspace
                project={project}
                chapters={chapters}
                proposals={globalDiffProposals}
                onInspectChapter={(chapter) => {
                  onChapter(chapter);
                  changeMode("manuscript");
                }}
              />
            ) : mode === "memory" ? (
              <StoryOverview
                project={project}
                chapters={chapters}
                storyMap={storyMap}
                memoryRun={memoryRun}
                projectMemory={projectMemory}
                onAnalyze={onAnalyzeMemory}
                onRetry={onRetryMemory}
                onProposalsChanged={(rows) => refreshProjectAfterProposal(rows)}
              />
            ) : mode === "manuscript" ? (
              <ManuscriptEditor
                project={project}
                activeChapter={activeChapter}
                activeContent={activeContent}
                agentDraft={manuscriptAgentDraft}
                onContentChange={onContentChange}
                onCreateChapter={onCreateChapter}
                onStartCharacter={() => {
                  newCharacter();
                  window.dispatchEvent(
                    new CustomEvent("story-studio-mobile-panel", {
                      detail: "agent",
                    }),
                  );
                  window.requestAnimationFrame(() => {
                    document
                      .querySelector<HTMLTextAreaElement>(
                        '.agent-compose textarea[aria-label="发送给 Agent 的消息"]',
                      )
                      ?.focus();
                  });
                }}
                onImport={onImport}
              />
            ) : mode === "story-map" || entityView === "graph" ? (
              activeChapter ? (
                <StoryGraphView
                  projectId={project.id}
                  chapterId={activeChapter.id}
                  chapterTitle={activeChapter.title}
                  graph={visibleGraph}
                  onNotice={onNotice}
                  onTargetChange={setGraphTarget}
                />
              ) : (
                <EmptyStoryGraph
                  onCreateChapter={onCreateChapter}
                  onShowCharacters={() => {
                    changeMode("characters");
                    setEntityView("table");
                  }}
                />
              )
            ) : (
              <div className="character-workbench">
                <div className="character-workbench-head">
                  <div>
                    <span className="eyebrow">人物资料</span>
                    <h2>
                      {isNewCharacter
                        ? "新增人物卷宗"
                        : editing?.name || "选择一位人物"}
                    </h2>
                    <p>
                      左侧选人物，中间按表格编辑；右侧 Agent
                      会把对话实时落到字段里。
                    </p>
                  </div>
                  <div
                    className="view-switch"
                    role="group"
                    aria-label="人物观察方式"
                  >
                    <button
                      className={entityView === "table" ? "is-active" : ""}
                      onClick={() => setEntityView("table")}
                    >
                      <Table2 size={14} /> 表格
                    </button>
                    <button
                      className={
                        (entityView as EntityViewMode) === "graph"
                          ? "is-active"
                          : ""
                      }
                      onClick={() => {
                        setEntityView("graph");
                        changeMode("characters");
                      }}
                    >
                      <Network size={14} /> 关系图
                    </button>
                  </div>
                </div>
                <CharacterGallery
                  characters={characters}
                  draftCharacters={draftCharacters}
                  onOpen={setExpandedCharacter}
                  onCreate={newCharacter}
                />
                {editing ? (
                  <CharacterForm
                    character={editing}
                    onChange={handleManualCharacterChange}
                    onSave={() => void saveCharacterMutation.mutate()}
                    onUpload={handleUpload}
                    onRemovePortrait={() => void removePortrait()}
                    busy={saveCharacterMutation.isPending}
                    agentDrafts={agentDrafts}
                  />
                ) : (
                  <div className="studio-empty">
                    <UserRound size={22} />
                    <strong>选择或新增一位人物</strong>
                    <p>
                      每本小说都有独立的人物卷宗；手动填写和 Agent
                      填写会共用同一张表。
                    </p>
                    <button
                      className="button button-primary"
                      onClick={newCharacter}
                    >
                      <Plus size={14} /> 新增人物
                    </button>
                  </div>
                )}
              </div>
            )}
          </main>
          <AgentDock
            project={project}
            target={target}
            character={editing}
            relationNodeCount={visibleGraph.nodes.length}
            chapters={chapters}
            activeChapter={activeChapter}
            activeContent={activeContent}
            onChapter={onChapter}
            assistantProvider={assistantProvider}
            onProposalPreview={handleProposalPreview}
            onProposalDismiss={dismissProposalPreview}
            onFollowProposal={followAgentProposal}
            onProposalApplied={async (proposal) => {
              (Array.isArray(proposal) ? proposal : [proposal]).forEach((item) =>
                dismissProposalPreview(item.id),
              );
              setManualPaths(new Set());
              await refreshProjectAfterProposal(proposal);
            }}
            autoOpen={autoOpenAgent}
            onNotice={onNotice}
            workMode={agentWorkMode}
            onWorkModeChange={setAgentWorkMode}
            onOpenGlobalDiff={() => changeMode("global-diff")}
            onMemoryRun={onMemoryRun}
          />
        </div>
        {expandedCharacter && (
          <CharacterDetailOverlay
            character={expandedCharacter}
            onClose={() => setExpandedCharacter(null)}
          />
        )}
      </div>
    </LayoutGroup>
  );
}

function StudioChapterList({
  chapters,
  activeChapterId,
  onChapter,
}: {
  chapters: Chapter[];
  activeChapterId?: string | null;
  onChapter: (chapter: Chapter) => void;
}) {
  return (
    <section
      className="studio-chapter-list"
      aria-labelledby="studio-chapter-list-title"
    >
      <div className="studio-chapter-list-head">
        <span id="studio-chapter-list-title">稿纸入口</span>
        <small>{chapters.length} 张</small>
      </div>
      {chapters.length ? (
        chapters.map((chapter) => (
          <button
            type="button"
            className={`studio-chapter-item ${chapter.id === activeChapterId ? "is-selected" : ""}`}
            key={chapter.id}
            onClick={() => onChapter(chapter)}
          >
            <span className="studio-chapter-number">
              {String(chapter.number).padStart(2, "0")}
            </span>
            <span>
              <strong>{chapter.title || "未命名稿纸"}</strong>
              <small>
                {chapter.summary
                  ? chapter.summary.slice(0, 28)
                  : chapter.summary_status === "running"
                    ? "摘要整理中…"
                    : "尚未整理摘要"}
              </small>
            </span>
            <span
              className={`studio-chapter-status chapter-${chapter.status || "draft"}`}
              title={studioChapterStatusLabel(chapter.status)}
            >
              {chapter.status === "generating" ? (
                <Loader2 size={12} className="spin" />
              ) : chapter.status === "failed" ||
                chapter.status === "rejected" ? (
                <CircleAlert size={12} />
              ) : chapter.status === "accepted" ||
                chapter.status === "confirmed" ? (
                <Check size={12} />
              ) : (
                <PencilLine size={12} />
              )}
            </span>
          </button>
        ))
      ) : (
        <p className="studio-list-empty">
          还没有稿纸；回到工作台新建一张空白稿纸。
        </p>
      )}
    </section>
  );
}

function targetMode(
  mode: StudioMode,
  character: CharacterCard,
): AgentTarget["type"] {
  return mode === "characters" && character.id ? "character" : "project";
}

function ManuscriptEditor({
  project,
  activeChapter,
  activeContent,
  agentDraft,
  onContentChange,
  onCreateChapter,
  onStartCharacter,
  onImport,
}: {
  project: Project;
  activeChapter: Chapter | null;
  activeContent: string;
  agentDraft?: ChapterAgentDraft | null;
  onContentChange: (content: string) => void;
  onCreateChapter: () => void;
  onStartCharacter: () => void;
  onImport: () => void;
}) {
  const agentWriting = Boolean(activeChapter && agentDraft);
  const displayedContent = agentDraft?.after ?? activeContent;
  return (
    <div className="manuscript-editor">
      <div className="manuscript-editor-head">
        <div>
          <span className="manuscript-kicker">写作</span>
          <h2>
            {activeChapter?.title ||
              (activeChapter
                ? `第 ${activeChapter.number} 章`
                : "开始写作")}
          </h2>
          <p>
            {activeChapter
              ? "这一页只放正文。边写边保存，右侧 Agent 会按当前稿纸协作。"
              : `${project.title} 还没有稿纸，先新建一张或导入旧稿。`}
          </p>
        </div>
      </div>
      {activeChapter ? (
        <section
          className={`manuscript-paper${agentWriting ? " is-agent-writing" : ""}`}
          aria-label="正文稿纸"
          aria-busy={agentDraft?.status === "building"}
        >
          <div className="manuscript-paper-meta">
            <span>第 {activeChapter.number} 章</span>
            {agentWriting ? (
              <span className="manuscript-live-status" role="status">
                <Sparkles size={12} />
                {agentDraft?.status === "building"
                  ? "Agent 正在写入正文"
                  : "正在自动保存正文"}
              </span>
            ) : (
              <span>{studioChapterStatusLabel(activeChapter.status)}</span>
            )}
          </div>
          <textarea
            data-chapter-id={activeChapter.id}
            className="manuscript-textarea"
            value={displayedContent}
            readOnly={agentWriting}
            onChange={(event) => onContentChange(event.target.value)}
            placeholder="从一个具体动作开始……"
            aria-label={`${
              activeChapter.title || `第 ${activeChapter.number} 章`
            }正文`}
            spellCheck
          />
          <div className="manuscript-paper-foot">
            <span>
              {agentWriting
                ? "正文正在当前稿纸中成形，完成后会直接保存。"
                : "正文变更会自动保存；右侧 Agent 会持续跟随当前章节。"}
            </span>
          </div>
        </section>
      ) : (
        <div className="manuscript-empty">
          <FileText size={24} />
          <strong>还没有稿纸</strong>
          <p>
            先铺一张空白稿纸，或把已有章节带进来；Agent
            也可以从全局设定开始搭骨架。
          </p>
          <div className="manuscript-empty-actions">
            <button
              type="button"
              className="button button-primary"
              onClick={onCreateChapter}
            >
              <Plus size={14} /> 开始写正文
            </button>
            <button
              type="button"
              className="button button-secondary"
              onClick={onStartCharacter}
            >
              <UserRound size={14} /> 和 Agent 定人物
            </button>
            <button
              type="button"
              className="button button-secondary"
              onClick={onImport}
            >
              <Upload size={14} /> 导入旧稿
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
function StoryOverview({
  project: projectInput,
  chapters,
  storyMap,
  memoryRun,
  projectMemory,
  onAnalyze,
  onRetry,
  onProposalsChanged,
}: {
  project: Project;
  chapters: Chapter[];
  storyMap: StoryMap;
  memoryRun: MemoryRun | null;
  projectMemory?: ProjectMemory | null;
  onAnalyze: () => void;
  onRetry?: () => void;
  onProposalsChanged: (proposals: AssistantProposal[]) => void | Promise<void>;
}) {
  const threads = storyMap.threads || [];
  const timeline = storyMap.timeline || [];
  const characters = storyMap.characters || [];
  const projectSummary = projectMemory?.project_summary;
  const project = projectInput;
  const summaryText =
    projectSummary?.summary_text ||
    "章节被确认后，故事摘要、人物关系与情节线会在这里形成。也可以手动整理一遍全书。";
  const memoryIsRunning = Boolean(
    memoryRun && ["queued", "running"].includes(memoryRun.status),
  );
  const memoryFailed = Boolean(
    memoryRun && ["failed", "stale", "needs_retry"].includes(memoryRun.status),
  );
  return (
    <div className="story-overview">
      <div className="story-overview-hero">
        <div>
          <span className="eyebrow">故事摘要</span>
          <h2>{project.title}</h2>
          <p>
            {project.logline ||
              "还没有一句话梗概。可以在右侧告诉 Agent：这本故事最想留下什么。"}
          </p>
        </div>
        <div className="story-overview-seal">
          <Sparkles size={19} />
          <span>
            记忆
            <br />卷
          </span>
        </div>
      </div>
      {memoryRun && (memoryIsRunning || memoryFailed) && (
        <section className={`memory-run-sheet ${memoryFailed ? "is-failed" : "is-running"}`}>
          <div className="memory-run-sheet-head">
            <span className="memory-run-number">
              v{Math.max(1, (projectSummary?.memory_epoch || 0) + 1)}
            </span>
            <div>
              <small>{memoryFailed ? "本次整理未发布" : "下一版正在形成"}</small>
              <strong>
                {memoryRun.phase_label ||
                  (memoryFailed ? "旧版全书记忆仍在安全使用" : "正在整理全书记忆")}
              </strong>
            </div>
            <b>{memoryFailed ? "—" : `${Math.round(memoryRun.progress || 0)}%`}</b>
          </div>
          <div
            className="memory-run-progress"
            role="progressbar"
            aria-label="全书记忆整理进度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={memoryFailed ? undefined : Math.round(memoryRun.progress || 0)}
          >
            <span style={{ width: `${memoryFailed ? 100 : Math.max(3, memoryRun.progress || 0)}%` }} />
          </div>
          <p>
            {memoryFailed
              ? memoryRun.error || "可以重新整理；已发布的旧版摘要不会被覆盖。"
              : `旧版全书记忆 v${projectSummary?.memory_epoch || 0} 仍在安全使用，并叠加最新变更账本；你可以继续写作和对话。`}
          </p>
          {memoryFailed && onRetry ? (
            <button type="button" className="button button-secondary button-small" onClick={onRetry}>
              <RefreshCw size={13} /> 重新整理
            </button>
          ) : null}
        </section>
      )}
      <div className="story-overview-grid">
        <section className="overview-card overview-summary-card">
          <div className="overview-card-head">
            <div>
              <span className="eyebrow">稳定版全书记忆</span>
              <h3>全书摘要 · v{projectSummary?.memory_epoch || 0}</h3>
            </div>
            <span
              className={`overview-status ${memoryRun?.status === "running" || memoryRun?.status === "queued" ? "is-running" : ""}`}
            >
              {memoryRun?.status === "running" || memoryRun?.status === "queued"
                ? "整理中"
                : projectSummary?.summary_text &&
                    projectSummary.status === "current"
                  ? "已整理"
                  : projectSummary
                    ? "需整理"
                    : "待建立"}
            </span>
          </div>
          <p className="overview-summary-text">{summaryText}</p>
          <div className="overview-card-actions">
            <button
              className="button button-secondary button-small"
              onClick={onAnalyze}
              disabled={
                memoryRun?.status === "running" ||
                memoryRun?.status === "queued"
              }
            >
              <RefreshCw size={13} />{" "}
              {memoryRun?.status === "running" || memoryRun?.status === "queued"
                ? "整理中…"
                : "分析全书"}
            </button>
            {projectSummary?.summary_text &&
            projectSummary.status === "current" ? (
              <span className="memory-applied-label">
                <Check size={13} /> 已自动更新
              </span>
            ) : null}
          </div>
        </section>
        <section className="overview-stat-card">
          <span className="overview-stat-icon">
            <UserRound size={15} />
          </span>
          <strong>{characters.length}</strong>
          <span>人物条目</span>
          <small>可继续补全人物弧</small>
        </section>
        <section className="overview-stat-card">
          <span className="overview-stat-icon">
            <Network size={15} />
          </span>
          <strong>{threads.length}</strong>
          <span>剧情线</span>
          <small>在关系图里连接主线</small>
        </section>
        <section className="overview-stat-card">
          <span className="overview-stat-icon">
            <Clock3 size={15} />
          </span>
          <strong>{timeline.length}</strong>
          <span>时间节点</span>
          <small>让前后发生顺序可见</small>
        </section>
      </div>
      <section className="overview-lanes">
        <div className="overview-section-head">
          <div>
            <span className="eyebrow">剧情线</span>
            <h3>正在发生的线索</h3>
          </div>
          <span>从图谱中继续编辑</span>
        </div>
        {threads.length ? (
          threads.slice(0, 5).map((thread) => (
            <div className="overview-lane" key={thread.id}>
              <span
                className="thread-color"
                style={{ background: thread.color || "#4F756B" }}
              />
              <div>
                <strong>{thread.title}</strong>
                <p>{thread.next_beat || thread.status || "等待下一拍"}</p>
              </div>
              <span className={`thread-status thread-${thread.status}`}>
                {thread.status === "active"
                  ? "进行中"
                  : thread.status || "待定"}
              </span>
            </div>
          ))
        ) : (
          <div className="overview-empty">
            <Network size={16} /> 暂无剧情线；完成一次故事整理后会显示在这里。
          </div>
        )}
      </section>
      {projectMemory?.chapter_summaries?.length ? (
        <section className="memory-chapter-index">
          <div className="overview-section-head">
            <div>
              <span className="eyebrow">章节记忆</span>
              <h3>已整理的章节</h3>
            </div>
            <span>{projectMemory.chapter_summaries.length} 章</span>
          </div>
          <div>
            {projectMemory.chapter_summaries
              .slice()
              .sort((left, right) => {
                const leftChapter = chapters.find((item) => item.id === left.chapter_id);
                const rightChapter = chapters.find((item) => item.id === right.chapter_id);
                return (leftChapter?.number || 0) - (rightChapter?.number || 0);
              })
              .map((summary) => {
                const chapter = chapters.find((item) => item.id === summary.chapter_id);
                return (
                  <article key={summary.id}>
                    <span>{chapter ? String(chapter.number).padStart(2, "0") : "—"}</span>
                    <div>
                      <strong>{chapter?.title || "章节记忆"}</strong>
                      <p>{summary.summary_text || "本章暂无摘要内容"}</p>
                    </div>
                    <small>v{summary.memory_epoch}</small>
                  </article>
                );
              })}
          </div>
        </section>
      ) : null}
      <MemoryProposalInbox
        projectId={project.id}
        memoryEpoch={projectMemory?.memory_epoch ?? project.memory_epoch}
        onChanged={onProposalsChanged}
      />
    </div>
  );
}
