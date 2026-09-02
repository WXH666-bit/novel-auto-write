import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AnimatePresence,
  LayoutGroup,
  motion,
  useReducedMotion,
} from "motion/react";
import type { PointerEvent as ReactPointerEvent } from "react";
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
  Bot,
  Check,
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileText,
  ImagePlus,
  Link2,
  Loader2,
  MessageCircle,
  Network,
  PencilLine,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
  Table2,
  Trash2,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import {
  applyAssistantProposal,
  applyAssistantProposals,
  createAssistantConversation,
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
  rejectAssistantProposal,
  rejectAssistantProposals,
  retryAssistantRun,
  saveStoryGraph,
  sendAssistantMessage,
  updateAssistantProposal,
  updateCharacter,
  uploadCharacterPortrait,
} from "./api";
import type {
  AgentPatch,
  AgentContextSnapshot,
  AgentSelectionSnapshot,
  AgentTarget,
  AssistantConversation,
  AssistantEvent,
  AssistantProposal,
  AssistantProposalActionDetail,
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

type AgentScopeMode = "project" | "chapter" | "selection";

export function resolveAgentMessageTarget(
  target: AgentTarget,
  scopeMode: AgentScopeMode,
  activeChapter: Pick<Chapter, "id"> | null,
): AgentTarget {
  // An entity selected in the people/graph surfaces is authoritative. The
  // chapter/selection scope is only a writing-mode refinement for a project.
  if (["character", "thread", "relationship"].includes(target.type)) {
    return target;
  }
  if (scopeMode !== "project" && activeChapter) {
    return {
      type: "chapter",
      id: activeChapter.id,
      chapter_id: activeChapter.id,
    };
  }
  return target.type === "project" ? { ...target, chapter_id: null } : target;
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
        x: 90 + (endpointIndex % 3) * 245,
        y: 90 + Math.floor(endpointIndex / 3) * 145,
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
            subtitle: String(patch.role || patch.motivation || "待你确认"),
            status:
              proposal.status === "building" ? "Agent 制作中" : "Agent 草稿",
            position: {
              x: 90 + (index % 3) * 245,
              y: 90 + Math.floor(index / 3) * 145,
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
            subtitle: String(patch.subtitle || "待你确认"),
            status:
              proposal.status === "building" ? "Agent 制作中" : "Agent 草稿",
            position: {
              x: 120 + (index % 3) * 245,
              y: 120 + Math.floor(index / 3) * 145,
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
        // A relationship proposal must stay reviewable even if its related
        // character proposal was rejected first. Temporary endpoint nodes keep
        // the dotted edge and its inline actions visible without creating any
        // formal story data.
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
  return working;
}

export type AgentBuildSummary = {
  total: number;
  building: boolean;
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
  const structureProposals = proposals.filter(
    (proposal) => isCharacterProposal(proposal) || isGraphProposal(proposal),
  );
  return {
    total: structureProposals.length,
    building: structureProposals.some(
      (proposal) => proposal.status === "building",
    ),
    characterCount: structureProposals.filter(
      (proposal) => isCharacterProposal(proposal) && !isGraphProposal(proposal),
    ).length,
    graphProposalCount: structureProposals.filter(isGraphProposal).length,
    nodeCount: graph.nodes.filter((node) => node.data?.agentDraft).length,
    edgeCount: graph.edges.filter((edge) => edge.data?.agentDraft).length,
    patchCount: structureProposals.reduce(
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
                ? "待复核"
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
              人物资料 · {character.status === "confirmed" ? "已确认" : character.status === "needs_review" ? "待复核" : "草稿"}
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
  const [agentFieldEditValues, setAgentFieldEditValues] = useState<
    Record<string, string>
  >({});
  const [agentFieldEditing, setAgentFieldEditing] = useState<Set<string>>(
    new Set(),
  );
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
              const editingDraft = agentFieldEditing.has(path);
              const editedDraftValue = Object.prototype.hasOwnProperty.call(
                agentFieldEditValues,
                path,
              )
                ? agentFieldEditValues[path]
                : draftValue;
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
                      {editingDraft ? (
                        field.multiline ? (
                          <textarea
                            rows={3}
                            value={editedDraftValue}
                            onChange={(event) =>
                              setAgentFieldEditValues((current) => ({
                                ...current,
                                [path]: event.target.value,
                              }))
                            }
                            aria-label={`${field.label} Agent 草稿手动修改`}
                          />
                        ) : (
                          <input
                            value={editedDraftValue}
                            onChange={(event) =>
                              setAgentFieldEditValues((current) => ({
                                ...current,
                                [path]: event.target.value,
                              }))
                            }
                            aria-label={`${field.label} Agent 草稿手动修改`}
                          />
                        )
                      ) : (
                        <small>
                          <del>{draft.before || "空白"}</del>
                          <ArrowRight size={11} />
                          <strong>{draftValue || "空白"}</strong>
                        </small>
                      )}
                      <AgentDraftActions
                        proposalId={draft.proposalId}
                        status={draft.status}
                        editing={editingDraft}
                        patches={[
                          {
                            op: "replace",
                            path,
                            value: editedDraftValue,
                          },
                        ]}
                        onManualEdit={() => {
                          setAgentFieldEditValues((current) => ({
                            ...current,
                            [path]: current[path] ?? draftValue,
                          }));
                          setAgentFieldEditing((current) => {
                            const next = new Set(current);
                            if (next.has(path)) next.delete(path);
                            else next.add(path);
                            return next;
                          });
                        }}
                      />
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
          <CheckCircle2 size={13} /> 可直接在字段下接受、拒绝或修改 Agent 草稿
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
  const [editing, setEditing] = useState(false);
  const [editedValues, setEditedValues] = useState<Record<string, string>>(
    {},
  );
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
              const value = Object.prototype.hasOwnProperty.call(
                editedValues,
                path,
              )
                ? editedValues[path]
                : Array.isArray(patch.value)
                  ? patch.value.join("、")
                  : String(patch.value ?? "");
              return (
                <div key={path}>
                  <dt>{patch.label || patchPath(path)}</dt>
                  <dd>
                    {editing ? (
                      <input
                        value={value}
                        onChange={(event) =>
                          setEditedValues((current) => ({
                            ...current,
                            [path]: event.target.value,
                          }))
                        }
                        aria-label={`${patch.label || patchPath(path)} Agent 草稿手动修改`}
                      />
                    ) : (
                      value || "空白"
                    )}
                  </dd>
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
            : `${proposal.patches.length} 项资料等待确认`}
        </span>
        <AgentDraftActions
          proposalId={proposal.id}
          status={proposal.status}
          editing={editing}
          disabled={proposal.status !== "proposed"}
          patches={
            editing
              ? proposalUpdatePatches(proposal, editedValues).filter((patch) =>
                  Object.prototype.hasOwnProperty.call(editedValues, patch.path),
                )
              : undefined
          }
          onManualEdit={() => setEditing((current) => !current)}
        />
        <small>
          {building
            ? "字段到达时会自动填入，完成后再确认"
            : "草稿只在此处预览，不会写入卷宗"}
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

function GraphNode({ data }: NodeProps<FlowNode>) {
  const [editing, setEditing] = useState(false);
  const [editedValues, setEditedValues] = useState<Record<string, string>>(
    {},
  );
  const draftPatches = data.draftPatches || [];
  const editablePatches = draftPatches
    .filter((patch) =>
      [
        "name",
        "role",
        "motivation",
        "personality",
        "label",
        "subtitle",
        "description",
        "status",
      ].includes(patchPath(patch.path)),
    )
    .slice(0, 2)
    .map((patch) => ({
      ...patch,
      value: Object.prototype.hasOwnProperty.call(editedValues, patch.path)
        ? editedValues[patch.path]
        : patch.value,
    }));
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
        <>
          {editing && (
            <div
              className="story-flow-node-draft-fields"
              onPointerDown={(event) => event.stopPropagation()}
            >
              {editablePatches.map((patch) => (
                <input
                  key={patch.path}
                  value={String(patch.value ?? "")}
                  onChange={(event) =>
                    setEditedValues((current) => ({
                      ...current,
                      [patch.path]: event.target.value,
                    }))
                  }
                  aria-label={`${patch.label || patch.path} Agent 草稿手动修改`}
                />
              ))}
            </div>
          )}
          <AgentDraftActions
            proposalId={data.proposalId}
            status={data.draftStatus}
            patches={
              editing
                ? data.draftPatches
                    ?.filter((patch) =>
                      Object.prototype.hasOwnProperty.call(
                        editedValues,
                        patch.path,
                      ),
                    )
                    .map((patch) => ({
                      ...patch,
                      value: editedValues[patch.path],
                    }))
                : undefined
            }
            disabled={data.draftStatus === "building"}
            onManualEdit={() => setEditing((current) => !current)}
            compact
            onPointerDown={(event) => event.stopPropagation()}
          />
        </>
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
  const [status, setStatus] = useState<StoryGraphEdge["status"]>("pending");
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
                    value={typeof edge.label === "string" ? edge.label : ""}
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
                    <option value="pending">待确认</option>
                    <option value="active">已生效</option>
                    <option value="needs_review">待复核</option>
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
          <option value="pending">待确认</option>
          <option value="active">已生效</option>
          <option value="needs_review">待复核</option>
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
  const [editing, setEditing] = useState(false);
  const [editedValues, setEditedValues] = useState<Record<string, string>>(
    {},
  );
  const source =
    nodes.find((node) => node.id === edge.source)?.data.label || "起点";
  const target =
    nodes.find((node) => node.id === edge.target)?.data.label || "终点";
  const patches = edge.data?.draftPatches || [];
  const displayPatches = patches.filter((patch) =>
    ["label", "relation_type", "relation", "kind"].includes(
      patchPath(patch.path),
    ),
  );
  const updatedPatches = editing
    ? patches
        .filter((patch) =>
          Object.prototype.hasOwnProperty.call(editedValues, patch.path),
        )
        .map((patch) => ({
          ...patch,
          value: editedValues[patch.path],
        }))
    : undefined;
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
            : `${edge.label || "建议关系"} · 虚线预览`}
        </small>
      </div>
      {editing && displayPatches.length > 0 && (
        <div className="story-graph-agent-draft-fields">
          {displayPatches.map((patch) => (
            <label key={patch.path}>
              <span>{patch.label || patchPath(patch.path)}</span>
              <input
                value={String(
                  Object.prototype.hasOwnProperty.call(
                    editedValues,
                    patch.path,
                  )
                    ? editedValues[patch.path]
                    : patch.value ?? "",
                )}
                onChange={(event) =>
                  setEditedValues((current) => ({
                    ...current,
                    [patch.path]: event.target.value,
                  }))
                }
                aria-label={`${patch.label || patchPath(patch.path)} Agent 草稿手动修改`}
              />
            </label>
          ))}
        </div>
      )}
      {edge.data?.proposalId && (
        <AgentDraftActions
          proposalId={edge.data.proposalId}
          status={edge.data.draftStatus}
          patches={updatedPatches}
          editing={editing}
          disabled={edge.data.draftStatus === "building"}
          onManualEdit={() => setEditing((current) => !current)}
        />
      )}
    </article>
  );
}

function StoryGraphView({
  projectId,
  chapterId,
  chapterTitle,
  graph,
  fallback,
  onNotice,
  onTargetChange,
}: {
  projectId: string;
  chapterId: string;
  chapterTitle: string;
  graph: StoryGraph;
  fallback: StoryGraph;
  onNotice?: StoryStudioProps["onNotice"];
  onTargetChange?: (target: AgentTarget) => void;
}) {
  const queryClient = useQueryClient();
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge>([]);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [edgeLabel, setEdgeLabel] = useState("");
  const [dirty, setDirty] = useState(false);
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
  void fallback;
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
        label: edge.label,
        type: "smoothstep",
        markerEnd:
          edge.direction === "directed"
            ? { type: MarkerType.ArrowClosed }
            : undefined,
        className: edge.data?.agentDraft ? "agent-draft-edge" : undefined,
        animated: Boolean(edge.data?.agentDraft),
        data: {
          ...(edge.data || {}),
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
    const hasAgentEdges = nextEdges.some((edge) => edge.data?.agentDraft);
    if (hasAgentEdges) {
      // Custom nodes expose their handles after XYFlow's ResizeObserver pass.
      // A structured proposal can deliver all patches in one event burst; if
      // its edge is installed in that same commit, XYFlow can miss the first
      // handle measurement. Keep durable edges visible, then attach drafts
      // just after the nodes have committed.
      setEdges(nextEdges.filter((edge) => !edge.data?.agentDraft));
    } else {
      setEdges(nextEdges);
    }
    const edgeTimer = hasAgentEdges
      ? window.setTimeout(() => setEdges(nextEdges), 120)
      : undefined;
    setDirty(false);
    setDeletedEdgeIds([]);
    return () => {
      if (edgeTimer !== undefined) window.clearTimeout(edgeTimer);
    };
  }, [sourceGraph, setEdges, setNodes]);
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId);
  useEffect(
    () => setEdgeLabel(String(selectedEdge?.label || "")),
    [selectedEdge],
  );
  const saveMutation = useMutation({
    mutationFn: () =>
      saveStoryGraph(
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
              label: typeof edge.label === "string" ? edge.label : undefined,
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
                (edge.data?.status as StoryGraphEdge["status"]) || "pending",
              version: edge.data?.version,
            })),
          version: graph.version,
          layout_version: graph.layout_version,
        },
        { deletedEdgeIds, expectedLayoutVersion: graph.layout_version },
      ),
    onSuccess: (saved) => {
      queryClient.setQueryData(["story-graph", projectId, chapterId], saved);
      setDirty(false);
      setDeletedEdgeIds([]);
    },
    onError: () =>
      notifyFallback(
        onNotice,
        "warning",
        "图谱暂存失败；你的本地编辑仍保留在当前页面。",
      ),
  });
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
            data: { kind: "relationship", status: "pending" },
          },
          current,
        ),
      );
      setDirty(true);
    },
    [setEdges],
  );
  const updateSelectedEdge = () => {
    if (!selectedEdgeId) return;
    setEdges((current) =>
      current.map((edge) =>
        edge.id === selectedEdgeId
          ? {
              ...edge,
              label: edgeLabel.trim() || "关系",
              data: { ...edge.data, status: "pending" },
            }
          : edge,
      ),
    );
    setDirty(true);
  };
  const patchEdge = (id: string, patch: Partial<FlowEdge>) => {
    setEdges((current) =>
      current.map((edge) => (edge.id === id ? { ...edge, ...patch } : edge)),
    );
    setDirty(true);
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
        label: label || kind,
        type: "smoothstep",
        markerEnd: directed ? { type: MarkerType.ArrowClosed } : undefined,
        data: { kind, status },
      },
    ]);
    setDirty(true);
  };
  const removeEdge = (id: string) => {
    if (edges.find((edge) => edge.id === id)?.data?.agentDraft) return;
    setEdges((current) => current.filter((edge) => edge.id !== id));
    if (!id.startsWith("edge-"))
      setDeletedEdgeIds((current) =>
        current.includes(id) ? current : [...current, id],
      );
    if (selectedEdgeId === id) setSelectedEdgeId(null);
    setDirty(true);
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
          <span className={dirty ? "graph-dirty" : "graph-saved"}>
            {dirty ? "有未保存连线" : "图谱已同步"}
          </span>
          <button
            className="button button-primary button-small"
            onClick={() => void saveMutation.mutateAsync()}
            disabled={saveMutation.isPending}
          >
            {saveMutation.isPending ? (
              <Loader2 size={13} className="spin" />
            ) : (
              <Check size={13} />
            )}{" "}
            保存图谱
          </button>
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
            key={`${nodes.map((node) => node.id).join("|")}::${edges
              .map((edge) => edge.id)
              .join("|")}`}
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
                setDirty(true);
            }}
            onEdgesChange={(changes) => {
              changes
                .filter((change) => change.type === "remove")
                .forEach((change) => removeEdge(change.id));
              onEdgesChange(changes);
              if (changes.some((change) => change.type !== "select"))
                setDirty(true);
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
                这条连线会以待确认状态保存；之后可在关系表里继续补充来源和说明。
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
          <CircleAlert size={13} /> 红色/虚线代表待确认
        </span>
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
  const [busyId, setBusyId] = useState("");
  const [notice, setNotice] = useState("");
  const applyOne = async (proposal: AssistantProposal) => {
    setBusyId(proposal.id);
    setNotice("");
    try {
      const applied = await applyAssistantProposal(
        projectId,
        proposal.conversation_id,
        proposal.id,
        {
          expected_version: proposal.base_version,
          expected_memory_epoch: proposal.base_memory_epoch ?? memoryEpoch,
        },
      );
      queryClient.setQueryData<AssistantProposal[]>(
        ["memory-proposals", projectId],
        (current) =>
          (current || []).map((item) =>
            item.id === applied.id
              ? {
                  ...item,
                  ...applied,
                  patches: applied.patches.length
                    ? applied.patches
                    : item.patches,
                }
              : item,
          ),
      );
      await onChanged([applied]);
      setNotice("提案已接受，人物、情节和故事图谱正在同步。");
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "提案接受失败，请刷新后重试。",
      );
    } finally {
      setBusyId("");
    }
  };
  const rejectOne = async (proposal: AssistantProposal) => {
    setBusyId(proposal.id);
    setNotice("");
    try {
      const rejected = await rejectAssistantProposal(
        projectId,
        proposal.conversation_id,
        proposal.id,
      );
      queryClient.setQueryData<AssistantProposal[]>(
        ["memory-proposals", projectId],
        (current) =>
          (current || []).map((item) =>
            item.id === rejected.id
              ? { ...item, ...rejected, patches: item.patches }
              : item,
          ),
      );
      await onChanged([rejected]);
      setNotice("提案已拒绝，当前正典保持不变。");
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "提案拒绝失败，请刷新后重试。",
      );
    } finally {
      setBusyId("");
    }
  };
  const applyAll = async () => {
    if (!proposals.length) return;
    setBusyId("bulk");
    setNotice("");
    try {
      const applied = await applyAssistantProposals(
        projectId,
        proposals.map((proposal) => proposal.id),
        {
          expected_memory_epoch: proposals[0].base_memory_epoch ?? memoryEpoch,
          expected_versions: Object.fromEntries(
            proposals
              .filter((proposal) => proposal.base_version != null)
              .map((proposal) => [
                proposal.id,
                proposal.base_version as number,
              ]),
          ),
        },
      );
      queryClient.setQueryData<AssistantProposal[]>(
        ["memory-proposals", projectId],
        (current) =>
          (current || []).map((item) => {
            const next = applied.find((proposal) => proposal.id === item.id);
            return next
              ? {
                  ...item,
                  ...next,
                  patches: next.patches.length ? next.patches : item.patches,
                }
              : item;
          }),
      );
      await onChanged(applied);
      setNotice(
        `已接受 ${applied.length || proposals.length} 条自动分析提案，项目资料已刷新。`,
      );
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "批量接受失败，请逐项复审。",
      );
    } finally {
      setBusyId("");
    }
  };
  const rejectAll = async () => {
    if (!proposals.length) return;
    setBusyId("bulk");
    setNotice("");
    try {
      const rejected = await rejectAssistantProposals(
        projectId,
        proposals.map((proposal) => proposal.id),
      );
      queryClient.setQueryData<AssistantProposal[]>(
        ["memory-proposals", projectId],
        (current) =>
          (current || []).map((item) => {
            const next = rejected.find((proposal) => proposal.id === item.id);
            return next ? { ...item, ...next, patches: item.patches } : item;
          }),
      );
      await onChanged(rejected);
      setNotice(
        `已拒绝 ${rejected.length || proposals.length} 条自动分析提案。`,
      );
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "批量拒绝失败，请逐项复审。",
      );
    } finally {
      setBusyId("");
    }
  };
  return (
    <section
      className="memory-proposal-inbox"
      aria-labelledby="memory-proposal-inbox-title"
    >
      <div className="memory-proposal-inbox-head">
        <div>
          <span className="eyebrow">待处理提案</span>
          <h3 id="memory-proposal-inbox-title">自动分析提案</h3>
          <p>
            摘要已经自动更新；人物、关系和情节线候选会先放在这里，确认后才进入正典。
          </p>
        </div>
        {proposals.length > 0 && (
          <div className="memory-proposal-inbox-actions">
            <span>{proposals.length} 条待处理</span>
            <button
              type="button"
              className="text-button"
              onClick={() => void applyAll()}
              disabled={Boolean(busyId)}
            >
              <CheckCircle2 size={12} /> 全部接受
            </button>
            <button
              type="button"
              className="text-button text-danger"
              onClick={() => void rejectAll()}
              disabled={Boolean(busyId)}
            >
              <X size={12} /> 全部拒绝
            </button>
          </div>
        )}
      </div>
      {proposalsQuery.isLoading ? (
        <div className="memory-proposal-empty">
          <Loader2 size={15} className="spin" /> 正在读取提案收件箱…
        </div>
      ) : proposals.length === 0 ? (
        <div className="memory-proposal-empty">
          <CheckCircle2 size={15} /> 暂无待处理的自动分析提案
        </div>
      ) : (
        <div className="memory-proposal-list">
          {proposals.map((proposal) => (
            <article className="memory-proposal-item" key={proposal.id}>
              <div>
                <strong>{proposal.summary}</strong>
                <small>
                  {proposal.operation || proposal.target_type || "结构化候选"} ·{" "}
                  {proposal.patches.length} 项变化
                </small>
              </div>
              <div className="memory-proposal-patches">
                {proposal.patches.slice(0, 4).map((patch, index) => (
                  <span key={`${patch.path}-${index}`}>
                    {patch.label || patch.path}：{String(patch.value ?? "—")}
                  </span>
                ))}
              </div>
              <div className="memory-proposal-item-actions">
                <button
                  type="button"
                  className="button button-primary button-small"
                  onClick={() => void applyOne(proposal)}
                  disabled={Boolean(busyId)}
                >
                  <Check size={12} /> 接受
                </button>
                <button
                  type="button"
                  className="button button-secondary button-small"
                  onClick={() => void rejectOne(proposal)}
                  disabled={Boolean(busyId)}
                >
                  <X size={12} /> 拒绝
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
      {notice && (
        <p className="memory-proposal-notice" role="status">
          {notice}
        </p>
      )}
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
  return visible || (String(content || "").trim() ? "已生成待确认改动。" : "");
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

export const ASSISTANT_PROPOSAL_ACTION_EVENT =
  "story-studio-agent-proposal-action";

export function dispatchAssistantProposalAction(
  detail: AssistantProposalActionDetail,
) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<AssistantProposalActionDetail>(
      ASSISTANT_PROPOSAL_ACTION_EVENT,
      { detail },
    ),
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

function AgentDraftActions({
  proposalId,
  patches,
  editing = false,
  onManualEdit,
  disabled = false,
  status,
  compact = false,
  primaryLabel = "接受改动",
  showManualEdit = true,
  onPointerDown,
}: {
  proposalId: string;
  patches?: AssistantProposalUpdatePatch[];
  editing?: boolean;
  onManualEdit?: () => void;
  disabled?: boolean;
  status?: AssistantProposalStatus;
  compact?: boolean;
  primaryLabel?: string;
  showManualEdit?: boolean;
  onPointerDown?: (event: ReactPointerEvent<HTMLDivElement>) => void;
}) {
  const building = status === "building";
  const actionsDisabled = disabled || status !== "proposed";
  return (
    <div
      className={`agent-draft-actions${compact ? " agent-draft-actions-compact" : ""}`}
      aria-label="Agent 草稿操作"
      onPointerDown={onPointerDown}
    >
      <button
        type="button"
        className="button button-primary button-small"
        onClick={() =>
          dispatchAssistantProposalAction({
            proposalId,
            action: "apply",
            patches: editing ? patches : undefined,
          })
        }
        disabled={actionsDisabled}
      >
        <Check size={12} /> {building ? "生成中…" : primaryLabel}
      </button>
      <button
        type="button"
        className="button button-secondary button-small"
        onClick={() =>
          dispatchAssistantProposalAction({ proposalId, action: "reject" })
        }
        disabled={actionsDisabled}
      >
        <X size={12} /> 拒绝
      </button>
      {showManualEdit && (
        <button
          type="button"
          className="text-button"
          onClick={onManualEdit}
          disabled={actionsDisabled}
        >
          <PencilLine size={12} /> {editing ? "完成手动修改" : "手动修改"}
        </button>
      )}
      {building && <small className="agent-draft-actions-status">提案生成中</small>}
    </div>
  );
}

function AgentLiveBuildRail({
  summary,
  activeSurface,
  onShowCharacters,
  onShowGraph,
}: {
  summary: AgentBuildSummary;
  activeSurface: "characters" | "graph" | null;
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
        <span>{summary.building ? "实时制作中" : "草稿已成形"}</span>
        <strong>
          {summary.building
            ? "Agent 正在铺开人物与关系"
            : "人物卡与故事图谱等待确认"}
        </strong>
        <small>
          人物 {summary.characterCount} · 节点 {summary.nodeCount} · 关系 {summary.edgeCount} · 已写入 {summary.patchCount} 项
        </small>
      </div>
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
      {summary.building && <span className="agent-live-build-ink" aria-hidden="true" />}
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
  conflictedProposalIds,
  onProposalApplied,
  relationNodeCount = 0,
  mobileVisible = true,
  autoOpen = false,
  onNotice,
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
  conflictedProposalIds: Set<string>;
  onProposalApplied: (proposal: AssistantProposal) => void | Promise<void>;
  relationNodeCount?: number;
  mobileVisible?: boolean;
  autoOpen?: boolean;
  onNotice?: StoryStudioProps["onNotice"];
}) {
  const queryClient = useQueryClient();
  const [conversation, setConversation] =
    useState<AssistantConversation | null>(null);
  const [conversationTargetKey, setConversationTargetKey] = useState("");
  const [messages, setMessages] = useState<AssistantConversation["messages"]>(
    [],
  );
  const [proposals, setProposals] = useState<AssistantProposal[]>([]);
  const [message, setMessage] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [status, setStatus] = useState<AssistantConversation["status"]>("idle");
  const [liveOutputStarted, setLiveOutputStarted] = useState(false);
  const [busyProposal, setBusyProposal] = useState("");
  const [notice, setNotice] = useState(
    autoOpen ? "Agent 已就位，可以从右侧开始描述你的想法。" : "",
  );
  const [historyOpen, setHistoryOpen] = useState(false);
  const [scopeMode, setScopeMode] = useState<
    "project" | "chapter" | "selection"
  >(() => (activeChapter ? "chapter" : "project"));
  const [selectionText, setSelectionText] = useState("");
  const [selectionSnapshot, setSelectionSnapshot] =
    useState<AgentSelectionSnapshot | null>(null);
  const [allowImage, setAllowImage] = useState(false);
  const [failedRunId, setFailedRunId] = useState("");
  const [retrying, setRetrying] = useState(false);
  const sequenceRef = useRef(0);
  const streamingMessageIdRef = useRef("");
  const streamingTextRef = useRef("");
  const lastAssistantReplyRef = useRef("");
  const knownAssistantMessageIdsRef = useRef<Set<string>>(new Set());
  const followProposalRef = useRef(onFollowProposal);
  const activeRunIdRef = useRef("");
  const knownRunIdsRef = useRef<Set<string>>(new Set());
  const liveSendRef = useRef<LiveSendState | null>(null);
  const initializedHistory = useRef(false);
  const targetKey = `${target.type}:${target.id}`;
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
    },
    [],
  );
  const activateLiveRun = useCallback((conversationId: string, runId: string) => {
    if (!runId) return;
    const pending = liveSendRef.current;
    if (!pending || pending.conversationId !== conversationId) return;
    pending.runId = runId;
    activeRunIdRef.current = runId;
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
  useEffect(() => {
    if (!activeChapter) {
      if (scopeMode !== "project") setScopeMode("project");
      setSelectionSnapshot(null);
      setSelectionText("");
      return;
    }
    if (["character", "thread", "relationship"].includes(target.type)) {
      // The selected entity is the message target; the range selector must not
      // accidentally turn an entity conversation into a chapter message.
      if (scopeMode !== "project") setScopeMode("project");
      return;
    }
  }, [activeChapter, scopeMode, target.type]);
  useEffect(() => {
    const handleEditorSelection = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          chapterId?: string;
          start?: number;
          end?: number;
        }>
      ).detail;
      if (
        target.type !== "project" ||
        !activeChapter ||
        detail?.chapterId !== activeChapter.id ||
        typeof detail.start !== "number" ||
        typeof detail.end !== "number" ||
        detail.end <= detail.start
      ) {
        return;
      }
      const selected = activeContent.slice(detail.start, detail.end);
      if (!selected.trim()) return;
      setSelectionText(selected);
      setSelectionSnapshot({
        chapter_id: activeChapter.id,
        base_revision_id: activeChapter.revision_id || null,
        start: detail.start,
        end: detail.end,
        hash: textHash(selected),
        quote: selected.slice(0, 240),
      });
      setScopeMode("selection");
      setNotice("已自动锁定当前选区；正文变化后需要重新选择。 ");
    };
    window.addEventListener("story-studio-editor-selection", handleEditorSelection);
    return () =>
      window.removeEventListener("story-studio-editor-selection", handleEditorSelection);
  }, [activeChapter, activeContent, target.type]);
  const conversationsQuery = useQuery({
    queryKey: ["assistant-conversations", project.id],
    queryFn: () => listAssistantConversations(project.id),
    staleTime: 15_000,
  });
  const conversationRows = conversationsQuery.data || [];

  const loadConversation = useCallback(
    async (conversationId: string) => {
      try {
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
        setConversation(loaded);
        setConversationTargetKey(`${inferredTarget.type}:${inferredTarget.id}`);
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
        setProposals(loadedProposals);
        loadedProposals
          .filter((proposal) => proposal.status === "proposed")
          .forEach(onProposalPreview);
        setStatus("idle");
        const runs = await listAssistantRuns(project.id, conversationId).catch(
          () => [],
        );
        const latestRun = runs.at(-1);
        knownRunIdsRef.current = new Set(
          runs.map((run) => run.id).filter(Boolean),
        );
        setFailedRunId(
          latestRun &&
            (latestRun.status === "failed" ||
              latestRun.status === "needs_retry")
            ? latestRun.id
            : "",
        );
        sequenceRef.current = 0;
      } catch (error) {
        notifyFallback(
          onNotice,
          "warning",
          error instanceof Error ? error.message : "历史会话读取失败。",
        );
      }
    },
    [onNotice, onProposalPreview, project.id, target],
  );

  useEffect(() => {
    if (initializedHistory.current || !conversationRows.length) return;
    initializedHistory.current = true;
    void loadConversation(conversationRows[0].id);
  }, [conversationRows, loadConversation]);

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
            notifyFallback(onNotice, "warning", "这条待确认改动已经处理或不存在。");
            return;
          }
          setProposals((current) => [
            selected,
            ...current.filter((item) => item.id !== selected.id),
          ]);
          onProposalPreview(selected);
          followProposalRef.current?.(selected);
          setNotice("已定位这条待确认改动；接受前只会显示为 Agent 草稿。");
        })
        .catch((error) =>
          notifyFallback(
            onNotice,
            "warning",
            error instanceof Error ? error.message : "待确认改动读取失败。",
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
          setLiveOutputStarted(true);
          setProposals((current) => [
            ...current.filter((item) => item.id !== event.proposal.id),
            event.proposal,
          ]);
          onProposalPreview(event.proposal);
          // Global requests can create people and relationships while the
          // author is still looking at a blank manuscript. Move the central
          // workspace with the live proposal stream so those pale-teal drafts
          // are visible without hunting for a secondary action. The matcher
          // gates this to the current send's run/cursor, not history replay.
          maybeFollowLiveProposal(event.proposal, event, conversationId);
        } else if (event.type === "proposal_patch") {
          setLiveOutputStarted(true);
          setProposals((current) => {
            const existing = current.find(
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
            onProposalPreview(preview);
            maybeFollowLiveProposal(preview, event, conversationId);
            return [
              ...current.filter((proposal) => proposal.id !== event.proposal_id),
              preview,
            ];
          });
        } else if (event.type === "proposal_completed") {
          setProposals((current) =>
            current.map((proposal) => {
              if (proposal.id !== event.proposal_id) return proposal;
              const next = { ...proposal, status: "proposed" as const };
              onProposalPreview(next);
              return next;
            }),
          );
          // Patch events are the live draft stream. Once the producer marks a
          // proposal ready, fetch the durable row once to pick up metadata
          // that was intentionally omitted from the skeleton/patch frames.
          void listAssistantProposals(project.id, conversationId)
            .then((rows) => {
              const next = rows.find(
                (proposal) => proposal.id === event.proposal_id,
              );
              if (!next) return;
              setProposals((current) => [
                ...current.filter((proposal) => proposal.id !== next.id),
                next,
              ]);
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
              liveSendRef.current = null;
            }
            setFailedRunId("");
            streamingMessageIdRef.current = "";
            streamingTextRef.current = "";
            setStreamingText("");
            setLiveOutputStarted(false);
            void loadConversation(conversationId);
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

  const startNewConversation = () => {
    proposals.forEach((proposal) => onProposalDismiss(proposal.id));
    setConversation(null);
    setConversationTargetKey("");
    setMessages([]);
    setProposals([]);
    setStreamingText("");
    setLiveOutputStarted(false);
    setStatus("idle");
    setFailedRunId("");
    streamingMessageIdRef.current = "";
    streamingTextRef.current = "";
    lastAssistantReplyRef.current = "";
    knownAssistantMessageIdsRef.current = new Set();
    activeRunIdRef.current = "";
    knownRunIdsRef.current = new Set();
    liveSendRef.current = null;
    setNotice("新对话已准备好。你可以描述人物、章节或选区需要怎样改变。");
    setHistoryOpen(false);
    sequenceRef.current = 0;
  };
  const ensureConversation = async () => {
    if (conversation && conversationTargetKey === targetKey)
      return conversation;
    const created = await createAssistantConversation(project.id, { target });
    const loaded = {
      ...created,
      target,
      messages: created.messages || [],
      proposals: created.proposals || [],
    };
    // Event sequences are scoped to a conversation. Reusing the previous
    // conversation's cursor can discard the beginning of a newly-created
    // stream (including run.started and the first live draft patches).
    sequenceRef.current = 0;
    setConversation(loaded);
    setConversationTargetKey(targetKey);
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
  const currentSelection =
    selectionSnapshot &&
    activeChapter &&
    activeChapter.id === selectionSnapshot.chapter_id
      ? activeContent.slice(selectionSnapshot.start, selectionSnapshot.end)
      : "";
  const selectionStale =
    scopeMode === "selection" &&
    (!selectionSnapshot ||
      !activeChapter ||
      activeChapter.id !== selectionSnapshot.chapter_id ||
      textHash(currentSelection) !== selectionSnapshot.hash ||
      (activeChapter.revision_id &&
        selectionSnapshot.base_revision_id &&
        activeChapter.revision_id !== selectionSnapshot.base_revision_id));
  const contextSnapshot = (): AgentContextSnapshot => {
    const base: AgentContextSnapshot = {
      chapter_id: scopeMode === "project" ? null : activeChapter?.id || null,
      base_revision_id:
        scopeMode === "project" ? null : activeChapter?.revision_id || null,
      selection: null,
    };
    if (scopeMode === "selection" && selectionSnapshot) {
      return {
        ...base,
        selection: selectionSnapshot,
        selection_start: selectionSnapshot.start,
        selection_end: selectionSnapshot.end,
        selection_hash: selectionSnapshot.hash,
        selected_text: selectionText,
      };
    }
    return base;
  };
  const send = async () => {
    const content = message.trim();
    if (!content || status === "streaming" || status === "queued") return;
    if (!activeChapter && scopeMode !== "project") {
      setNotice(
        "当前还没有章节；可以切换到全局设定，先和 Agent 搭建故事骨架。",
      );
      return;
    }
    if (selectionStale) {
      setNotice("正文选区已变化，请重新读取选区后再生成，避免覆盖新内容。");
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
    const snapshot = contextSnapshot();
    const messageTarget = resolveAgentMessageTarget(
      target,
      scopeMode,
      activeChapter,
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
      }).then(({ message: savedMessage, run: startedRun }) => {
        activateLiveRun(
          active.id,
          startedRun.id || savedMessage.run_id || "",
        );
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
  const decide = async (
    proposal: AssistantProposal,
    action: "apply" | "reject",
    editedPatches?: AssistantProposalUpdatePatch[],
  ) => {
    if (busyProposal) return;
    if (proposal.status !== "proposed") {
      setNotice(
        proposal.status === "building"
          ? "提案仍在生成中，请等它准备好后再确认。"
          : "这条提案已不能确认，请重新生成最新建议。",
      );
      return;
    }
    if (action === "apply" && conflictedProposalIds.has(proposal.id)) {
      setNotice("这条建议与尚未保存的手动修改冲突。请先保存或撤回手动修改，再重新生成建议。");
      return;
    }
    setBusyProposal(proposal.id);
    try {
      let actionableProposal = proposal;
      if (action === "apply" && editedPatches?.length) {
        const patches = editedPatches.map(({ op, path, value }) => ({
          op,
          path,
          value,
        }));
        const updated = await updateAssistantProposal(
          project.id,
          proposal.id,
          patches,
        );
        actionableProposal = {
          ...proposal,
          ...updated,
          patches: updated.patches.length ? updated.patches : proposal.patches,
        };
        setProposals((current) =>
          current.map((item) =>
            item.id === actionableProposal.id ? actionableProposal : item,
          ),
        );
        onProposalPreview(actionableProposal);
      }
      const next =
        action === "apply"
          ? await applyAssistantProposal(
              project.id,
              conversation?.id || actionableProposal.conversation_id || "",
              actionableProposal.id,
              {
                expected_version: actionableProposal.base_version,
                expected_memory_epoch: actionableProposal.base_memory_epoch,
              },
            )
          : await rejectAssistantProposal(
              project.id,
              conversation?.id || proposal.conversation_id || "",
              proposal.id,
            );
      setProposals((current) =>
        current.map((item) =>
          item.id === next.id
            ? {
                ...item,
                ...next,
                patches: next.patches.length ? next.patches : item.patches,
              }
            : item,
        ),
      );
      onProposalDismiss(proposal.id);
      await queryClient.invalidateQueries({
        queryKey: ["project-attention", project.id],
      });
      if (action === "apply") {
        await onProposalApplied(next);
        if (
          next.target.type === "chapter" ||
          proposal.target.type === "chapter"
        ) {
          await queryClient.invalidateQueries({
            queryKey: ["chapters", project.id],
          });
          setNotice("正文提案已应用；请在章节中复审 diff，确认后再继续生成。");
        }
      } else {
        setNotice("提案已拒绝，正文和设定保持不变。");
      }
    } catch (error) {
      const code = apiErrorCode(error);
      notifyFallback(
        onNotice,
        "warning",
        code === "proposal_conflict"
          ? "正文或选区已经变化，无法安全应用；请重新读取选区并生成。"
          : error instanceof Error
            ? error.message
            : "提案状态更新失败。\n",
      );
    } finally {
      setBusyProposal("");
    }
  };
  useEffect(() => {
    const handleProposalAction = (event: Event) => {
      const detail = (event as CustomEvent<AssistantProposalActionDetail>).detail;
      if (!detail || (detail.projectId && detail.projectId !== project.id)) {
        return;
      }
      const proposal = proposals.find((item) => item.id === detail.proposalId);
      if (!proposal) return;
      void decide(proposal, detail.action, detail.patches);
    };
    window.addEventListener(ASSISTANT_PROPOSAL_ACTION_EVENT, handleProposalAction);
    return () =>
      window.removeEventListener(
        ASSISTANT_PROPOSAL_ACTION_EVENT,
        handleProposalAction,
      );
  }, [decide, project.id, proposals]);
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
  const hasConversationProvider = Boolean(conversation?.id);
  const effectiveProviderName = hasConversationProvider
    ? conversation?.provider_name || "当前会话连接"
    : assistantProvider?.name || "尚未添加模型";
  const canSeeImage = hasConversationProvider
    ? conversation?.provider_capabilities?.vision === true
    : assistantProvider?.capabilities?.vision === true;
  const targetLabel =
    target.type === "character"
      ? character?.name || "未命名人物"
      : target.type === "thread"
        ? "当前情节线"
        : target.type === "relationship"
          ? "当前关系"
          : target.type === "chapter"
            ? activeChapter?.title || "当前稿纸"
            : scopeMode === "selection"
              ? "当前选区"
              : scopeMode === "chapter"
                ? activeChapter?.title || "当前稿纸"
                : project.title;
  const targetScopeLabel =
    target.type === "character"
      ? "人物设定"
      : target.type === "thread"
        ? "情节线"
        : target.type === "relationship"
          ? "人物关系"
          : target.type === "chapter"
            ? "当前章节"
            : scopeMode === "selection"
              ? "当前选区"
              : scopeMode === "chapter"
                ? "当前章节"
                : "全局故事";
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
    status === "queued"
      ? "正在恢复上下文与未完成内容"
      : `正在梳理${targetScopeLabel}与当前上下文`;
  return (
    <aside
      className={`agent-dock ${mobileVisible ? "" : "mobile-panel-hidden"}`}
    >
      <div className="agent-dock-head">
        <div>
          <h2>
            <Bot size={17} /> 和 Agent 一起写
          </h2>
          <small className="agent-provider-state">
            <span className={`status-dot ${canSeeImage ? "green" : ""}`} />
            {effectiveProviderName}
            {canSeeImage ? " · 可看图" : ""}
          </small>
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
            <MessageCircle size={14} />
            {conversationRows.length > 0 && <small>{conversationRows.length}</small>}
          </button>
          <button
            type="button"
            className="agent-head-action"
            onClick={startNewConversation}
            aria-label="新建 Agent 对话"
            title="新对话"
          >
            <Plus size={15} />
          </button>
        </div>
      </div>
      {historyOpen && (
        <div
          className="agent-history"
          role="listbox"
          aria-label="历史 Agent 会话"
        >
          {conversationRows.length ? (
            conversationRows.map((row) => (
              <button
                type="button"
                role="option"
                aria-selected={row.id === conversation?.id}
                key={row.id}
                onClick={() => {
                  setHistoryOpen(false);
                  void loadConversation(row.id);
                }}
              >
                <strong>{row.title || "故事设定助手"}</strong>
                <small>
                  {row.updated_at ? formatDate(row.updated_at) : "历史会话"}
                </small>
              </button>
            ))
          ) : (
            <span>还没有持久会话</span>
          )}
        </div>
      )}
      <details className="agent-scope agent-context" aria-label="Agent 作用范围">
        <summary>
          <span>
            <PencilLine size={13} /> <strong>{targetLabel}</strong>
          </span>
          <small>
            {targetScopeLabel} · {scopeMode === "project"
              ? "整本故事"
              : scopeMode === "selection"
                ? "当前选区"
                : "当前稿纸"}
          </small>
        </summary>
        <div
          className="agent-scope-tabs"
          role="group"
          aria-label="Agent 作用范围选择"
        >
          <button
            type="button"
            className={scopeMode === "project" ? "is-active" : ""}
            onClick={() => setScopeMode("project")}
          >
            全局设定
          </button>
          <button
            type="button"
            className={scopeMode === "chapter" ? "is-active" : ""}
            onClick={() => setScopeMode("chapter")}
            disabled={!activeChapter}
          >
            当前章节
          </button>
          <button
            type="button"
            className={scopeMode === "selection" ? "is-active" : ""}
            onClick={() => setScopeMode("selection")}
            disabled={!activeChapter}
          >
            当前选区
          </button>
        </div>
        {chapters.length > 0 && (
          <select
            className="agent-chapter-select"
            aria-label="Agent 当前章节"
            value={activeChapter?.id || ""}
            onChange={(event) => {
              const chapter = chapters.find(
                (item) => item.id === event.target.value,
              );
              if (chapter) {
                onChapter(chapter);
                setSelectionSnapshot(null);
                setSelectionText("");
                setScopeMode("chapter");
                notifyFallback(
                  onNotice,
                  "info",
                  `已切换 Agent 当前章节：${chapter.title}`,
                );
              }
            }}
          >
            <option value="">选择章节</option>
            {chapters.map((chapter) => (
              <option value={chapter.id} key={chapter.id}>
                {String(chapter.number).padStart(2, "0")} · {chapter.title}
              </option>
            ))}
          </select>
        )}
        {scopeMode === "selection" && (
          <div className="agent-selection-box">
            <p className="agent-selection-hint">
              选区由中央稿纸自动锁定；需要更换时，请在正文中重新选择一段文字。
            </p>
            <textarea
              value={selectionText}
              onChange={(event) => {
                const text = event.target.value;
                setSelectionText(text);
                if (activeChapter && text) {
                  const start = activeContent.indexOf(text);
                  setSelectionSnapshot(
                    start >= 0
                      ? {
                          chapter_id: activeChapter.id,
                          base_revision_id: activeChapter.revision_id || null,
                          start,
                          end: start + text.length,
                          hash: textHash(text),
                          quote: text.slice(0, 240),
                        }
                      : null,
                  );
                }
              }}
              placeholder="也可以粘贴当前章节中的连续片段"
              rows={2}
              aria-label="当前选区文字"
            />
            {selectionStale && (
              <small className="agent-selection-warning">
                <CircleAlert size={12} /> 选区已变化，请重新读取后再发送
              </small>
            )}
          </div>
        )}
      </details>
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
      {proposals.some(isPreviewProposal) && (
        <p className="agent-change-notice" role="status">
          改动已显示在当前内容
        </p>
      )}
      {notice && (
        <div
          className={`agent-notice agent-notice-${status === "error" ? "error" : "info"}`}
        >
          <CircleAlert size={13} />
          <span>{notice}</span>
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
        </div>
      )}
      </div>
      <div className="agent-compose">
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void send();
            }
          }}
          placeholder="告诉 Agent 你想补充、修改或连接什么…"
          rows={3}
          aria-label="发送给 Agent 的消息"
        />
        <div>
          <small>⌘ / Ctrl + Enter 发送</small>
          <button
            type="button"
            className="button button-primary button-small"
            onClick={() => void send()}
            disabled={
              !message.trim() || status === "streaming" || status === "queued"
            }
          >
            <Send size={13} />{" "}
            {status === "streaming" || status === "queued"
              ? "生成中…"
              : "发送"}
          </button>
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
  initialMode = "characters",
  autoOpenAgent = false,
  onContentChange,
  onCreateChapter,
  onImport,
  onModeChange,
  onChapter,
  onNotice,
}: StoryStudioProps) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<StudioMode>(initialMode);
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
  }, [project.id]);
  const charactersQuery = useQuery({
    queryKey: ["characters", project.id],
    queryFn: () => getCharacters(project.id),
    enabled: mode === "characters" || mode === "story-map",
  });
  const graphQuery = useQuery({
    queryKey: ["story-graph", project.id, activeChapter?.id],
    queryFn: () => getStoryGraph(project.id, activeChapter?.id),
    enabled:
      Boolean(activeChapter) &&
      (mode === "story-map" ||
        (mode === "characters" && entityView === "graph")),
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
      notifyFallback(onNotice, "info", "Agent 草稿已直接显示为图谱中的虚线节点和连线。");
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
      notifyFallback(onNotice, "info", "Agent 草稿已直接显示在人物卷宗中，确认后才会生效。");
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
  const manuscriptAgentDraft = useMemo(
    () => chapterAgentDraft(activeChapter, activeContent, liveProposals),
    [activeChapter, activeContent, liveProposals],
  );
  const agentDrafts = useMemo(
    () => characterAgentDrafts(editing, liveProposals, manualPaths),
    [editing, liveProposals, manualPaths],
  );
  const conflictedProposalIds = useMemo(
    () =>
      new Set(
        Object.values(agentDrafts)
          .filter((draft) => draft.conflict)
          .map((draft) => draft.proposalId),
      ),
    [agentDrafts],
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
                <small>{characters.length}</small>
              </button>
              <button
                className={mode === "story-map" ? "is-active" : ""}
                onClick={() => {
                  changeMode("story-map");
                  setEntityView("graph");
                }}
                title="故事图谱"
              >
                <Network size={15} /> 故事图谱
              </button>
            </nav>
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
                          ? "待确认"
                          : "草稿"}
                    </span>
                  </button>
                ))}
                {characters.length === 0 && (
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
            <AgentLiveBuildRail
              summary={agentBuild}
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
            {mode === "manuscript" ? (
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
            ) : mode === "story-map" && activeChapter ? (
              <StoryGraphView
                projectId={project.id}
                chapterId={activeChapter.id}
                chapterTitle={activeChapter.title}
                graph={visibleGraph}
                fallback={storyGraphFallback}
                onNotice={onNotice}
                onTargetChange={setGraphTarget}
              />
            ) : entityView === "graph" && activeChapter ? (
              <StoryGraphView
                projectId={project.id}
                chapterId={activeChapter.id}
                chapterTitle={activeChapter.title}
                graph={visibleGraph}
                fallback={storyGraphFallback}
                onNotice={onNotice}
                onTargetChange={setGraphTarget}
              />
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
            conflictedProposalIds={conflictedProposalIds}
            onProposalApplied={async (proposal) => {
              dismissProposalPreview(proposal.id);
              setManualPaths(new Set());
              await refreshProjectAfterProposal(proposal);
            }}
            autoOpen={autoOpenAgent}
            onNotice={onNotice}
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
              title={chapter.status || "draft"}
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
  const [editingDraftId, setEditingDraftId] = useState("");
  const [editedDraft, setEditedDraft] = useState("");
  const [draftExpanded, setDraftExpanded] = useState(false);
  const draftRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!agentDraft || agentDraft.proposalId !== editingDraftId) {
      setEditingDraftId("");
      setEditedDraft(agentDraft?.editValue || "");
    }
  }, [agentDraft?.editValue, agentDraft?.proposalId, editingDraftId]);
  useEffect(() => {
    setDraftExpanded(false);
  }, [agentDraft?.proposalId]);
  useEffect(() => {
    if (draftExpanded) {
      draftRef.current?.scrollIntoView({ block: "nearest" });
    }
  }, [draftExpanded]);
  const editingAgentDraft = Boolean(
    agentDraft && editingDraftId === agentDraft.proposalId,
  );
  return (
    <div className="manuscript-editor">
      <div className="manuscript-editor-head">
        <div>
          <span className="manuscript-kicker">写作</span>
          <h2>
            {activeChapter?.title || (activeChapter ? `第 ${activeChapter.number} 章` : "开始写作")}
          </h2>
          <p>
            {activeChapter
              ? "这一页只放正文。边写边保存，右侧 Agent 会按当前稿纸协作。"
              : `${project.title} 还没有稿纸，先新建一张或导入旧稿。`}
          </p>
        </div>
        {activeChapter && agentDraft && (
          <section className="manuscript-review-callout" aria-label="Agent 正文改动操作">
            <div className="manuscript-review-copy">
              <span><Sparkles size={13} /> Agent 已准备一处改动</span>
              <button
                type="button"
                className="text-button"
                onClick={() => setDraftExpanded((expanded) => !expanded)}
                aria-expanded={draftExpanded}
              >
                {draftExpanded ? "收起对比" : "查看改动"}
              </button>
            </div>
            <AgentDraftActions
              proposalId={agentDraft.proposalId}
              status={agentDraft.status}
              editing={editingAgentDraft}
              disabled={agentDraft.status === "building"}
              primaryLabel="同意改变"
              showManualEdit={false}
              patches={
                editingAgentDraft
                  ? [
                      {
                        op: "replace",
                        path: agentDraft.editPath,
                        value: editedDraft,
                      },
                    ]
                  : undefined
              }
            />
          </section>
        )}
      </div>
      {activeChapter && agentDraft && draftExpanded && (
        <section
          ref={draftRef}
          className="manuscript-agent-draft"
          aria-label="Agent 正文草稿对比"
        >
          <div className="manuscript-agent-draft-head">
            <div>
              <span className="eyebrow"><Sparkles size={12} /> 改动对比</span>
              <h3>{agentDraft.summary || "待确认的正文修改"}</h3>
            </div>
            <button
              type="button"
              className="text-button manuscript-draft-edit"
              onClick={() => {
                if (editingAgentDraft) {
                  setEditedDraft(agentDraft.editValue);
                  setEditingDraftId("");
                  return;
                }
                setEditedDraft(agentDraft.editValue);
                setEditingDraftId(agentDraft.proposalId);
              }}
              disabled={agentDraft.status !== "proposed"}
            >
              <PencilLine size={12} />
              {editingAgentDraft ? "放弃调整" : "手动调整"}
            </button>
          </div>
          <div className="manuscript-agent-diff" aria-label="Agent 正文差异">
            <div>
              <small>原文片段</small>
              <del>{agentDraft.before.slice(agentDraft.start, agentDraft.end) || "（空白）"}</del>
            </div>
            <ArrowRight size={14} aria-hidden="true" />
            <div>
              <small>Agent 替换</small>
              <strong>{agentDraft.replacement || "（删除）"}</strong>
            </div>
          </div>
          <label className="manuscript-agent-preview-label">
            <span>{editingAgentDraft ? "调整后的内容" : "完整草稿"}</span>
            <textarea
              className="manuscript-agent-preview"
              value={editingAgentDraft ? editedDraft : agentDraft.after}
              readOnly={!editingAgentDraft}
              onChange={(event) => setEditedDraft(event.target.value)}
              rows={8}
              aria-label="Agent 正文草稿预览"
            />
          </label>
        </section>
      )}
      {activeChapter ? (
        <section className="manuscript-paper" aria-label="正文稿纸">
          <div className="manuscript-paper-meta">
            <span>第 {activeChapter.number} 章</span>
            <span>{activeChapter.status === "draft" ? "草稿" : activeChapter.status || "未标记"}</span>
          </div>
          <textarea
            data-chapter-id={activeChapter.id}
            className="manuscript-textarea"
            value={activeContent}
            onChange={(event) => onContentChange(event.target.value)}
            onSelect={(event) => {
              const start = event.currentTarget.selectionStart;
              const end = event.currentTarget.selectionEnd;
              if (end > start) {
                window.dispatchEvent(
                  new CustomEvent("story-studio-editor-selection", {
                    detail: {
                      chapterId: activeChapter.id,
                      start,
                      end,
                    },
                  }),
                );
              }
            }}
            placeholder="从一个具体动作开始……"
            aria-label={`${activeChapter.title || `第 ${activeChapter.number} 章`}正文`}
            spellCheck
          />
          <div className="manuscript-paper-foot">
            <span>正文变更会自动保存；需要时可在右侧选中一段文字交给 Agent。</span>
          </div>
        </section>
      ) : (
        <div className="manuscript-empty">
          <FileText size={24} />
          <strong>还没有稿纸</strong>
          <p>先铺一张空白稿纸，或把已有章节带进来；Agent 也可以从全局设定开始搭骨架。</p>
          <div className="manuscript-empty-actions">
            <button type="button" className="button button-primary" onClick={onCreateChapter}>
              <Plus size={14} /> 开始写正文
            </button>
            <button type="button" className="button button-secondary" onClick={onStartCharacter}>
              <UserRound size={14} /> 和 Agent 定人物
            </button>
            <button type="button" className="button button-secondary" onClick={onImport}>
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
  storyMap,
  memoryRun,
  projectMemory,
  onAnalyze,
  onProposalsChanged,
}: {
  project: Project;
  storyMap: StoryMap;
  memoryRun: MemoryRun | null;
  projectMemory?: ProjectMemory | null;
  onAnalyze: () => void;
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
      <div className="story-overview-grid">
        <section className="overview-card overview-summary-card">
          <div className="overview-card-head">
            <div>
              <span className="eyebrow">当前摘要</span>
              <h3>故事摘要</h3>
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
                    ? "待复核"
                    : "待建立"}
            </span>
          </div>
          <p>{summaryText}</p>
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
      <MemoryProposalInbox
        projectId={project.id}
        memoryEpoch={projectMemory?.memory_epoch ?? project.memory_epoch}
        onChanged={onProposalsChanged}
      />
    </div>
  );
}
