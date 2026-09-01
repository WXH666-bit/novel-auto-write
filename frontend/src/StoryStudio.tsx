import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "motion/react";
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
  ArrowLeft,
  ArrowRight,
  Bot,
  Check,
  CheckCircle2,
  CircleAlert,
  Clock3,
  ImagePlus,
  LayoutGrid,
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
  listenAssistantEvents,
  apiErrorCode,
  rejectAssistantProposal,
  rejectAssistantProposals,
  saveStoryGraph,
  sendAssistantMessage,
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

type NoticeTone = "info" | "success" | "warning" | "error";

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
  fields: Array<{ key: keyof CharacterCard; label: string; placeholder: string; multiline?: boolean }>;
}> = [
  {
    title: "角色坐标",
    fields: [
      { key: "name", label: "姓名", placeholder: "角色在故事里的称呼" },
      { key: "aliases", label: "别名", placeholder: "用逗号分隔，例如：小林、灯塔守" },
      { key: "role", label: "身份 / 作用", placeholder: "主角、引路人、隐藏反派…" },
      { key: "age", label: "年龄", placeholder: "可写年龄或人生阶段" },
      { key: "gender", label: "性别", placeholder: "可留空" },
      { key: "pronouns", label: "称谓", placeholder: "他 / 她 / 祂 / 名字" },
      { key: "occupation", label: "职业", placeholder: "他如何在世界里谋生？" },
    ],
  },
  {
    title: "可被看见的部分",
    fields: [
      { key: "appearance", label: "外貌", placeholder: "让读者能记住的细节…", multiline: true },
      { key: "personality", label: "性格", placeholder: "习惯、底色、矛盾…", multiline: true },
      { key: "background", label: "背景", placeholder: "来自哪里，经历过什么？", multiline: true },
      { key: "abilities", label: "能力 / 局限", placeholder: "擅长什么，又付出什么代价？", multiline: true },
      { key: "voice", label: "说话方式", placeholder: "节奏、口头禅、避讳…", multiline: true },
    ],
  },
  {
    title: "推动故事的部分",
    fields: [
      { key: "motivation", label: "深层动机", placeholder: "他真正想要什么？", multiline: true },
      { key: "goals", label: "目标", placeholder: "这一阶段正在追逐什么？", multiline: true },
      { key: "conflict_fears", label: "冲突 / 恐惧", placeholder: "阻碍和最害怕失去的是什么？", multiline: true },
      { key: "arc", label: "人物弧", placeholder: "他会如何改变？", multiline: true },
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

function fieldValue(character: CharacterCard, key: keyof CharacterCard): string {
  const value = character[key];
  if (Array.isArray(value)) return value.join("、");
  if (value === undefined || value === null) return "";
  return String(value);
}

function patchPath(path: string) {
  return path.replace(/^(character|人物)\.?/, "").replace(/^profile\.?/, "");
}

function applyCharacterPatch(character: CharacterCard, patch: AgentPatch): CharacterCard {
  const key = patchPath(patch.path) as keyof CharacterCard;
  if (!(key in character)) return character;
  if (key === "aliases" || key === "tags") {
    const value = Array.isArray(patch.value)
      ? patch.value.map(String)
      : String(patch.value ?? "").split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean);
    return { ...character, [key]: value } as CharacterCard;
  }
  if (typeof character[key] === "object" && key !== "portrait" && key !== "custom_fields") {
    return character;
  }
  return { ...character, [key]: String(patch.value ?? "") } as CharacterCard;
}

function notifyFallback(onNotice: StoryStudioProps["onNotice"], tone: NoticeTone, message: string) {
  onNotice?.(tone, message);
}

function formatDate(value?: string) {
  if (!value) return "刚刚";
  return value.replace("T", " ").slice(0, 16);
}

function CharacterPortrait({ character, large = false }: { character: CharacterCard; large?: boolean }) {
  if (character.portrait?.url) {
    return <img className={large ? "character-portrait large" : "character-portrait"} src={character.portrait.url} alt={character.portrait.alt || `${character.name || "人物"}头像`} />;
  }
  return (
    <span className={large ? "character-portrait character-portrait-placeholder large" : "character-portrait character-portrait-placeholder"} aria-hidden="true">
      {(character.name || "人").slice(0, 1)}
    </span>
  );
}

function CharacterCardView({ character, onOpen }: { character: CharacterCard; onOpen: () => void }) {
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
      <div className="character-card-image"><CharacterPortrait character={character} /><span className={`character-card-status status-${character.status}`}>{character.status === "confirmed" ? "已入典" : character.status === "active" ? "已生效" : character.status === "needs_review" ? "待复核" : "草稿"}</span></div>
      <div className="character-card-copy">
        <span className="eyebrow">CHARACTER DOSSIER</span>
        <h3>{character.name || "未命名人物"}</h3>
        <p>{character.role || character.motivation || character.goals || "还没有角色摘要。"}</p>
        <div className="character-card-tags">{(character.tags.length ? character.tags : ["待补全"]).slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div>
      </div>
      <span className="character-card-open"><ArrowRight size={15} /></span>
    </motion.article>
  );
}

function CharacterDetailOverlay({ character, onClose }: { character: CharacterCard; onClose: () => void }) {
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
    <motion.div ref={layerRef} className="character-detail-layer" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} role="dialog" aria-modal="true" aria-labelledby="character-detail-title">
      <button className="character-detail-scrim" onClick={onClose} aria-label="关闭人物详情" />
      <motion.article layoutId={`character-card-${character.id}`} className="character-detail-card" transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 280, damping: 28 }}>
        <div className="character-detail-hero"><CharacterPortrait character={character} large /><div><span className="eyebrow">CHARACTER DOSSIER / {character.status.toUpperCase()}</span><h2 id="character-detail-title">{character.name || "未命名人物"}</h2><p>{character.role || "尚未填写身份"}</p></div><button ref={closeRef} className="quiet-icon" onClick={onClose} aria-label="关闭人物详情"><X size={17} /></button></div>
        <div className="character-detail-body">
          <div className="character-detail-stats"><span><small>别名</small><strong>{character.aliases.join(" / ") || "—"}</strong></span><span><small>年龄</small><strong>{character.age || "—"}</strong></span><span><small>称谓</small><strong>{character.pronouns || "—"}</strong></span></div>
          <div className="character-detail-grid">{fieldGroups.flatMap((group) => group.fields).filter((field) => field.key !== "name" && field.key !== "aliases" && fieldValue(character, field.key)).map((field) => <section key={String(field.key)}><span>{field.label}</span><p>{fieldValue(character, field.key)}</p></section>)}</div>
          <div className="character-detail-footer"><span><CheckCircle2 size={13} /> 设定与正文保持项目隔离</span><span>更新于 {formatDate(character.updated_at)}</span></div>
        </div>
      </motion.article>
    </motion.div>
  );
}

function CharacterImagePicker({ character, onFile, onRemove, busy }: { character: CharacterCard; onFile: (file: File) => void; onRemove: () => void; busy: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div className="character-image-picker">
      <div className="character-image-preview"><CharacterPortrait character={character} large /></div>
      <div><strong>人物肖像</strong><p>支持 JPG、PNG、WebP，单张不超过 10 MB。</p><div className="character-image-actions"><button type="button" className="button button-secondary button-small" onClick={() => inputRef.current?.click()} disabled={busy}><Upload size={13} /> {character.portrait ? "更换图片" : "上传图片"}</button>{character.portrait && <button type="button" className="text-button text-danger" onClick={onRemove} disabled={busy}><Trash2 size={13} /> 移除</button>}</div></div>
      <input ref={inputRef} type="file" accept="image/png,image/jpeg,image/webp" className="visually-hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) onFile(file); event.target.value = ""; }} />
    </div>
  );
}

function CharacterForm({ character, onChange, onSave, onUpload, onRemovePortrait, busy, changedPaths }: { character: CharacterCard; onChange: (next: CharacterCard) => void; onSave: () => void; onUpload: (file: File) => void; onRemovePortrait: () => void; busy: boolean; changedPaths: Set<string> }) {
  const update = (key: keyof CharacterCard, value: string) => {
    if (key === "aliases" || key === "tags") {
      onChange({ ...character, [key]: value.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean) } as CharacterCard);
    } else onChange({ ...character, [key]: value } as CharacterCard);
  };
  const [customKey, setCustomKey] = useState("");
  const [customValue, setCustomValue] = useState("");
  const addCustom = () => {
    const key = customKey.trim();
    if (!key) return;
    onChange({ ...character, custom_fields: { ...character.custom_fields, [key]: customValue } });
    setCustomKey("");
    setCustomValue("");
  };
  return (
    <div className="character-form-shell">
      <CharacterImagePicker character={character} onFile={onUpload} onRemove={onRemovePortrait} busy={busy} />
      {fieldGroups.map((group) => <section className="character-form-group" key={group.title}><div className="character-form-group-head"><span className="eyebrow">{group.title}</span><small>表格填写 · 可让 Agent 代填</small></div><div className="character-form-table">{group.fields.map((field) => { const path = String(field.key); const changed = changedPaths.has(path); return <label className={`character-field ${changed ? "is-agent-updated" : ""}`} key={path}><span>{field.label}</span>{field.multiline ? <textarea rows={3} value={fieldValue(character, field.key)} onChange={(event) => update(field.key, event.target.value)} placeholder={field.placeholder} /> : <input value={fieldValue(character, field.key)} onChange={(event) => update(field.key, event.target.value)} placeholder={field.placeholder} />}{changed && <em><Sparkles size={11} /> Agent刚刚填写</em>}</label>; })}</div></section>)}
      <section className="character-form-group"><div className="character-form-group-head"><span className="eyebrow">标签与自定义字段</span><small>给这张卷宗留下你的索引</small></div><label className="character-field"><span>标签</span><input value={character.tags.join("、")} onChange={(event) => update("tags", event.target.value)} placeholder="主角、灯塔、秘密" /></label><div className="custom-field-add"><input value={customKey} onChange={(event) => setCustomKey(event.target.value)} placeholder="字段名，例如：秘密" /><input value={customValue} onChange={(event) => setCustomValue(event.target.value)} placeholder="字段内容" /><button type="button" className="button button-secondary button-small" onClick={addCustom}><Plus size={13} /> 添加字段</button></div>{Object.entries(character.custom_fields).map(([key, value]) => <div className="custom-field-row" key={key}><strong>{key}</strong><input value={value} onChange={(event) => onChange({ ...character, custom_fields: { ...character.custom_fields, [key]: event.target.value } })} /><button type="button" className="quiet-icon" onClick={() => { const next = { ...character.custom_fields }; delete next[key]; onChange({ ...character, custom_fields: next }); }} aria-label={`删除${key}`}><X size={13} /></button></div>)}</section>
      <div className="character-form-footer"><span><CheckCircle2 size={13} /> 手动保存会写入当前人物设定；Agent 提案仍需单独应用</span><button type="button" className="button button-primary" onClick={onSave} disabled={busy}>{busy ? <Loader2 size={14} className="spin" /> : <Check size={14} />} {busy ? "保存中…" : "保存人物卷宗"}</button></div>
    </div>
  );
}

function CharacterGallery({ characters, onOpen, onCreate }: { characters: CharacterCard[]; onOpen: (character: CharacterCard) => void; onCreate: () => void }) {
  return (
    <section className="character-gallery" aria-labelledby="character-gallery-title">
      <div className="character-gallery-head">
        <div><span className="eyebrow">PORTRAIT INDEX</span><h3 id="character-gallery-title">人物卡片</h3><p>点击卡片展卷查看；选择表格继续编辑。</p></div>
        <button type="button" className="button button-secondary button-small" onClick={onCreate}><Plus size={13} /> 新增卡片</button>
      </div>
      {characters.length ? <div className="character-gallery-grid">{characters.map((character) => <CharacterCardView character={character} key={character.id} onOpen={() => onOpen(character)} />)}</div> : <div className="character-gallery-empty"><UserRound size={17} /><span>还没有人物卡片</span><small>先写下一个名字，或让 Agent 从故事里找出第一位人物。</small></div>}
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
};
type FlowEdge = Edge<FlowEdgeData>;

function GraphNode({ data }: NodeProps<FlowNode>) {
  return <div className={`story-flow-node node-${data.type}`}><Handle type="target" position={Position.Left} /><div className="story-flow-node-icon">{data.image_url ? <img src={data.image_url} alt="" /> : data.type === "character" ? <UserRound size={14} /> : data.type === "thread" ? <Network size={14} /> : <PencilLine size={14} />}</div><div><strong>{data.label}</strong>{data.subtitle && <small>{data.subtitle}</small>}</div>{data.status && <em>{data.status}</em>}<Handle type="source" position={Position.Right} /></div>;
}

const graphNodeTypes = { character: GraphNode, thread: GraphNode, event: GraphNode };

function fallbackGraph(project: Project, characters: CharacterCard[], storyMap: StoryMap): StoryGraph {
  const nodes: StoryGraphNode[] = characters.map((character, index) => ({
    id: `character-${character.id}`,
    type: "character",
    label: character.name || "未命名人物",
    subtitle: character.role,
    image_url: character.portrait?.url,
    status: character.status,
    position: { x: 80 + (index % 3) * 250, y: 80 + Math.floor(index / 3) * 150 },
    ref_id: character.id,
    character_id: character.id,
    source_refs: character.source_refs,
    data: { is_fallback: true, ref_id: character.id, character_id: character.id, source_refs: character.source_refs },
  }));
  (storyMap.threads || []).slice(0, 6).forEach((thread, index) => nodes.push({
    id: `thread-${thread.id}`,
    type: "thread",
    label: thread.title,
    subtitle: thread.next_beat,
    status: thread.status,
    position: { x: 100 + (index % 2) * 300, y: 440 + Math.floor(index / 2) * 140 },
    ref_id: thread.id,
    plot_thread_id: thread.id,
    data: { is_fallback: true, ref_id: thread.id, thread_id: thread.id, plot_thread_id: thread.id },
  }));
  (storyMap.timeline || []).slice(0, 8).forEach((event, index) => nodes.push({
    id: `event-${event.id}`,
    type: "event",
    label: event.title,
    subtitle: event.date_label || event.description,
    status: event.status,
    position: { x: 680 + (index % 2) * 260, y: 80 + Math.floor(index / 2) * 150 },
    ref_id: event.id,
    chapter_id: event.chapter_id,
    source_refs: event.source_ref ? [event.source_ref] : [],
    data: { is_fallback: true, ref_id: event.id, chapter_id: event.chapter_id, source_refs: event.source_ref ? [event.source_ref] : [], event_id: event.id },
  }));
  return { nodes, edges: [] };
}

function graphNodeIdentity(node: StoryGraphNode) {
  const ref = node.character_id || node.plot_thread_id || node.chapter_id || node.ref_id;
  return `${node.type}:${ref || node.id}`;
}

function mergeStoryGraphs(persisted: StoryGraph, fallback: StoryGraph): StoryGraph {
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
    edges: persisted.edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)),
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
  onAdd: (source: string, target: string, kind: string, label: string, directed: boolean, status: StoryGraphEdge["status"]) => void;
}) {
  const [source, setSource] = useState(nodes[0]?.id || "");
  const [target, setTarget] = useState(nodes[1]?.id || nodes[0]?.id || "");
  const [kind, setKind] = useState("related");
  const [label, setLabel] = useState("");
  const [directed, setDirected] = useState(true);
  const [status, setStatus] = useState<StoryGraphEdge["status"]>("pending");
  const nodeLabel = (id: string) => nodes.find((node) => node.id === id)?.data.label || "未命名节点";
  return <section className="relation-table-shell" aria-labelledby="relation-table-title">
    <div className="relation-table-head"><div><span className="eyebrow">RELATION INDEX</span><h3 id="relation-table-title">关系表</h3><p>表格与关系图共享同一份数据，键盘也能完整编辑。</p></div><span className="relation-table-count">{edges.length} 条连线</span></div>
    <div className="relation-table-wrap"><table className="relation-table"><caption className="visually-hidden">人物和情节关系</caption><thead><tr><th scope="col">来源</th><th scope="col">目标</th><th scope="col">类型</th><th scope="col">标签</th><th scope="col">方向</th><th scope="col">状态</th><th scope="col"><span className="visually-hidden">操作</span></th></tr></thead><tbody>{edges.map((edge) => <tr key={edge.id}><td><select aria-label="关系来源" value={edge.source} onChange={(event) => onPatch(edge.id, { source: event.target.value })}>{nodes.map((node) => <option value={node.id} key={node.id}>{nodeLabel(node.id)}</option>)}</select></td><td><select aria-label="关系目标" value={edge.target} onChange={(event) => onPatch(edge.id, { target: event.target.value })}>{nodes.map((node) => <option value={node.id} key={node.id}>{nodeLabel(node.id)}</option>)}</select></td><td><input aria-label="关系类型" value={String(edge.data?.kind || "related")} onChange={(event) => onPatch(edge.id, { data: { ...edge.data, kind: event.target.value } })} /></td><td><input aria-label="关系标签" value={typeof edge.label === "string" ? edge.label : ""} onChange={(event) => onPatch(edge.id, { label: event.target.value })} placeholder="可选" /></td><td><select aria-label="关系方向" value={edge.markerEnd ? "directed" : "undirected"} onChange={(event) => onPatch(edge.id, { markerEnd: event.target.value === "directed" ? { type: MarkerType.ArrowClosed } : undefined })}><option value="directed">有向</option><option value="undirected">无向</option></select></td><td><select aria-label="关系状态" value={String(edge.data?.status || "pending")} onChange={(event) => onPatch(edge.id, { data: { ...edge.data, status: event.target.value as StoryGraphEdge["status"] } })}><option value="pending">待确认</option><option value="active">已生效</option><option value="needs_review">待复核</option><option value="confirmed">已确认</option><option value="draft">草稿</option></select></td><td><button className="quiet-icon" type="button" onClick={() => onDelete(edge.id)} aria-label={`删除${nodeLabel(edge.source)}与${nodeLabel(edge.target)}的关系`}><Trash2 size={14} /></button></td></tr>)}</tbody></table>{edges.length === 0 && <div className="relation-table-empty"><Link2 size={17} /> 还没有关系；从下方添加第一条。</div>}</div>
    <form className="relation-add-row" onSubmit={(event) => { event.preventDefault(); if (!source || !target || source === target) return; onAdd(source, target, kind.trim() || "related", label.trim(), directed, status); setLabel(""); }}><strong>新增关系</strong><select aria-label="新关系来源" value={source} onChange={(event) => setSource(event.target.value)}>{nodes.map((node) => <option value={node.id} key={node.id}>{nodeLabel(node.id)}</option>)}</select><ArrowRight size={14} aria-hidden="true" /><select aria-label="新关系目标" value={target} onChange={(event) => setTarget(event.target.value)}>{nodes.map((node) => <option value={node.id} key={node.id}>{nodeLabel(node.id)}</option>)}</select><input aria-label="新关系类型" value={kind} onChange={(event) => setKind(event.target.value)} placeholder="类型" /><input aria-label="新关系标签" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="标签" /><select aria-label="新关系方向" value={directed ? "directed" : "undirected"} onChange={(event) => setDirected(event.target.value === "directed")}><option value="directed">有向</option><option value="undirected">无向</option></select><select aria-label="新关系状态" value={status} onChange={(event) => setStatus(event.target.value as StoryGraphEdge["status"])}><option value="pending">待确认</option><option value="active">已生效</option><option value="needs_review">待复核</option></select><button className="button button-secondary button-small" type="submit" disabled={nodes.length < 2 || source === target}><Plus size={13} /> 添加</button></form>
  </section>;
}

function StoryGraphView({ projectId, graph, fallback, onNotice }: { projectId: string; graph: StoryGraph; fallback: StoryGraph; onNotice?: StoryStudioProps["onNotice"] }) {
  const queryClient = useQueryClient();
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge>([]);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [edgeLabel, setEdgeLabel] = useState("");
  const [dirty, setDirty] = useState(false);
  const [graphViewMode, setGraphViewMode] = useState<EntityViewMode>(() => typeof window !== "undefined" && window.innerWidth < 760 ? "table" : "graph");
  const [deletedEdgeIds, setDeletedEdgeIds] = useState<string[]>([]);
  // StoryStudio merges durable nodes with derived fallback nodes before this
  // view renders. Keep the canvas bound to that merged snapshot so a project
  // with no persisted nodes still shows its derived story map.
  const sourceGraph = graph;
  void fallback;
  useEffect(() => {
    setNodes(sourceGraph.nodes.map((node) => ({
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
    })));
    setEdges(sourceGraph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.label,
      type: "smoothstep",
      markerEnd: edge.direction === "directed" ? { type: MarkerType.ArrowClosed } : undefined,
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
    })));
    setDirty(false);
    setDeletedEdgeIds([]);
  }, [sourceGraph, setEdges, setNodes]);
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId);
  useEffect(() => setEdgeLabel(String(selectedEdge?.label || "")), [selectedEdge]);
  const saveMutation = useMutation({
    mutationFn: () => saveStoryGraph(projectId, {
      nodes: nodes.map((node) => ({
        id: node.id,
        type: node.type as StoryGraphNode["type"],
        label: String(node.data.label || "未命名节点"),
        subtitle: typeof node.data.subtitle === "string" ? node.data.subtitle : undefined,
        image_url: typeof node.data.image_url === "string" ? node.data.image_url : undefined,
        status: typeof node.data.status === "string" ? node.data.status : undefined,
        position: node.position,
        data: node.data,
        ref_id: typeof node.data.ref_id === "string" ? node.data.ref_id : undefined,
        character_id: typeof node.data.character_id === "string" ? node.data.character_id : undefined,
        chapter_id: typeof node.data.chapter_id === "string" ? node.data.chapter_id : undefined,
        plot_thread_id: typeof node.data.plot_thread_id === "string" ? node.data.plot_thread_id : undefined,
        source_refs: node.data.source_refs,
        version: node.data.version,
      })),
      edges: edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: typeof edge.label === "string" ? edge.label : undefined,
        kind: String(edge.data?.kind || "relationship"),
        relation_type: String(edge.data?.kind || edge.data?.relation_type || "related"),
        direction: edge.markerEnd ? "directed" : "undirected",
        directed: Boolean(edge.markerEnd),
        weight: edge.data?.weight,
        source_refs: edge.data?.source_refs,
        data: edge.data,
        status: (edge.data?.status as StoryGraphEdge["status"]) || "pending",
        version: edge.data?.version,
      })),
      version: graph.version,
      layout_version: graph.layout_version,
    }, { deletedEdgeIds, expectedLayoutVersion: graph.layout_version }),
    onSuccess: (saved) => {
      queryClient.setQueryData(["story-graph", projectId], saved);
      setDirty(false);
      setDeletedEdgeIds([]);
    },
    onError: () => notifyFallback(onNotice, "warning", "图谱暂存失败；你的本地编辑仍保留在当前页面。"),
  });
  const connect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target || connection.source === connection.target) return;
    setEdges((current) => addEdge({ ...connection, id: `edge-${Date.now()}`, type: "smoothstep", label: "新关系", markerEnd: { type: MarkerType.ArrowClosed }, data: { kind: "relationship", status: "pending" } }, current));
    setDirty(true);
  }, [setEdges]);
  const updateSelectedEdge = () => {
    if (!selectedEdgeId) return;
    setEdges((current) => current.map((edge) => edge.id === selectedEdgeId ? { ...edge, label: edgeLabel.trim() || "关系", data: { ...edge.data, status: "pending" } } : edge));
    setDirty(true);
  };
  const patchEdge = (id: string, patch: Partial<FlowEdge>) => {
    setEdges((current) => current.map((edge) => edge.id === id ? { ...edge, ...patch } : edge));
    setDirty(true);
  };
  const addRelation = (source: string, target: string, kind: string, label: string, directed: boolean, status: StoryGraphEdge["status"]) => {
    setEdges((current) => [...current, { id: `edge-${Date.now()}`, source, target, label: label || kind, type: "smoothstep", markerEnd: directed ? { type: MarkerType.ArrowClosed } : undefined, data: { kind, status } }]);
    setDirty(true);
  };
  const removeEdge = (id: string) => {
    setEdges((current) => current.filter((edge) => edge.id !== id));
    if (!id.startsWith("edge-")) setDeletedEdgeIds((current) => current.includes(id) ? current : [...current, id]);
    if (selectedEdgeId === id) setSelectedEdgeId(null);
    setDirty(true);
  };
  const removeSelectedEdge = () => {
    if (!selectedEdgeId) return;
    removeEdge(selectedEdgeId);
  };
  return <div className="story-graph-shell"><div className="story-graph-toolbar"><div><span className="eyebrow">EDITABLE STORY MAP</span><h2>把人物和情节线牵在一起</h2></div><div className="story-graph-actions"><div className="view-switch" role="group" aria-label="图谱观察方式"><button className={graphViewMode === "graph" ? "is-active" : ""} onClick={() => setGraphViewMode("graph")}><Network size={13} /> 关系图</button><button className={graphViewMode === "table" ? "is-active" : ""} onClick={() => setGraphViewMode("table")}><Table2 size={13} /> 关系表</button></div><span className={dirty ? "graph-dirty" : "graph-saved"}>{dirty ? "有未保存连线" : "图谱已同步"}</span><button className="button button-primary button-small" onClick={() => void saveMutation.mutateAsync()} disabled={saveMutation.isPending}>{saveMutation.isPending ? <Loader2 size={13} className="spin" /> : <Check size={13} />} 保存图谱</button></div></div>{graphViewMode === "table" ? <GraphRelationTable edges={edges} nodes={nodes} onPatch={patchEdge} onDelete={removeEdge} onAdd={addRelation} /> : <div className="story-graph-canvas"><ReactFlow nodes={nodes} edges={edges} nodeTypes={graphNodeTypes} onNodesChange={(changes) => { onNodesChange(changes); if (changes.some((change) => change.type === "position" || change.type === "remove" || change.type === "add")) setDirty(true); }} onEdgesChange={(changes) => { changes.filter((change) => change.type === "remove").forEach((change) => removeEdge(change.id)); onEdgesChange(changes); if (changes.some((change) => change.type !== "select")) setDirty(true); }} onConnect={connect} onEdgeClick={(_, edge) => setSelectedEdgeId(edge.id)} fitView minZoom={0.25} maxZoom={1.7} aria-label="可编辑故事关系图"><Background color="rgba(74,84,78,.13)" gap={28} size={1} /><Controls showInteractive={false} /><MiniMap nodeColor={(node) => node.type === "character" ? "#A9463B" : node.type === "thread" ? "#4F756B" : "#927A4D"} /></ReactFlow>{selectedEdge && <aside className="story-edge-inspector"><div><span className="eyebrow">RELATIONSHIP THREAD</span><button className="quiet-icon" onClick={() => setSelectedEdgeId(null)} aria-label="关闭关系编辑"><X size={14} /></button></div><label className="field"><span>关系标签</span><input value={edgeLabel} onChange={(event) => setEdgeLabel(event.target.value)} placeholder="例如：互相试探" /></label><p>这条连线会以待确认状态保存；之后可在关系表里继续补充来源和说明。</p><div><button className="button button-secondary button-small" onClick={updateSelectedEdge}><Check size={13} /> 更新标签</button><button className="text-button text-danger" onClick={removeSelectedEdge}><Trash2 size={13} /> 删除连线</button></div></aside>}</div>}<div className="story-graph-help"><span><Link2 size={13} /> 从节点边缘拖出连线</span><span><Table2 size={13} /> 关系表可编辑完整字段</span><span><CircleAlert size={13} /> 红色/虚线代表待确认</span></div></div>;
}

type ProposalBulkActions = {
  firstId: string;
  ids: string[];
  busy: boolean;
  apply: () => void;
  reject: () => void;
};

function AgentProposalCard({ proposal, onApply, onReject, busy, bulk }: { proposal: AssistantProposal; onApply: () => void; onReject: () => void; busy: boolean; bulk?: ProposalBulkActions | null }) {
  return <article className={`agent-proposal agent-proposal-${proposal.status}`}><div className="agent-proposal-head"><span className="agent-proposal-icon"><Sparkles size={13} /></span><div><strong>{proposal.summary}</strong><small>{proposal.patches.length} 个字段变化 · {proposal.status === "applied" ? "已应用" : proposal.status === "rejected" ? "已拒绝" : "等待决定"}</small></div></div><div className="agent-patch-list">{proposal.patches.slice(0, 5).map((patch, index) => <div className="agent-patch-row" key={`${patch.path}-${index}`}><span>{patch.label || patch.path}</span><strong>{String(patch.value ?? "—")}</strong></div>)}</div>{proposal.status === "proposed" && <div className="agent-proposal-actions"><button className="button button-primary button-small" onClick={onApply} disabled={busy}><Check size={13} /> 应用到表格</button><button className="button button-secondary button-small" onClick={onReject} disabled={busy}><X size={13} /> 拒绝</button></div>}{bulk?.firstId === proposal.id && bulk.ids.length > 1 && <div className="agent-proposal-bulk"><span>共 {bulk.ids.length} 条待处理</span><button type="button" className="text-button" onClick={bulk.apply} disabled={bulk.busy}><CheckCircle2 size={12} /> 全部接受</button><button type="button" className="text-button text-danger" onClick={bulk.reject} disabled={bulk.busy}><X size={12} /> 全部拒绝</button></div>}</article>;
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
  const proposals = (proposalsQuery.data || []).filter((proposal) => proposal.status === "proposed");
  const [busyId, setBusyId] = useState("");
  const [notice, setNotice] = useState("");
  const applyOne = async (proposal: AssistantProposal) => {
    setBusyId(proposal.id);
    setNotice("");
    try {
      const applied = await applyAssistantProposal(projectId, proposal.conversation_id, proposal.id, {
        expected_version: proposal.base_version,
        expected_memory_epoch: proposal.base_memory_epoch ?? memoryEpoch,
      });
      queryClient.setQueryData<AssistantProposal[]>(["memory-proposals", projectId], (current) =>
        (current || []).map((item) => item.id === applied.id ? { ...item, ...applied, patches: applied.patches.length ? applied.patches : item.patches } : item),
      );
      await onChanged([applied]);
      setNotice("提案已接受，人物、情节和故事图谱正在同步。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "提案接受失败，请刷新后重试。");
    } finally {
      setBusyId("");
    }
  };
  const rejectOne = async (proposal: AssistantProposal) => {
    setBusyId(proposal.id);
    setNotice("");
    try {
      const rejected = await rejectAssistantProposal(projectId, proposal.conversation_id, proposal.id);
      queryClient.setQueryData<AssistantProposal[]>(["memory-proposals", projectId], (current) =>
        (current || []).map((item) => item.id === rejected.id ? { ...item, ...rejected, patches: item.patches } : item),
      );
      await onChanged([rejected]);
      setNotice("提案已拒绝，当前正典保持不变。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "提案拒绝失败，请刷新后重试。");
    } finally {
      setBusyId("");
    }
  };
  const applyAll = async () => {
    if (!proposals.length) return;
    setBusyId("bulk");
    setNotice("");
    try {
      const applied = await applyAssistantProposals(projectId, proposals.map((proposal) => proposal.id), {
        expected_memory_epoch: proposals[0].base_memory_epoch ?? memoryEpoch,
        expected_versions: Object.fromEntries(proposals.filter((proposal) => proposal.base_version != null).map((proposal) => [proposal.id, proposal.base_version as number])),
      });
      queryClient.setQueryData<AssistantProposal[]>(["memory-proposals", projectId], (current) =>
        (current || []).map((item) => {
          const next = applied.find((proposal) => proposal.id === item.id);
          return next ? { ...item, ...next, patches: next.patches.length ? next.patches : item.patches } : item;
        }),
      );
      await onChanged(applied);
      setNotice(`已接受 ${applied.length || proposals.length} 条自动分析提案，项目资料已刷新。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "批量接受失败，请逐项复审。");
    } finally {
      setBusyId("");
    }
  };
  const rejectAll = async () => {
    if (!proposals.length) return;
    setBusyId("bulk");
    setNotice("");
    try {
      const rejected = await rejectAssistantProposals(projectId, proposals.map((proposal) => proposal.id));
      queryClient.setQueryData<AssistantProposal[]>(["memory-proposals", projectId], (current) =>
        (current || []).map((item) => {
          const next = rejected.find((proposal) => proposal.id === item.id);
          return next ? { ...item, ...next, patches: item.patches } : item;
        }),
      );
      await onChanged(rejected);
      setNotice(`已拒绝 ${rejected.length || proposals.length} 条自动分析提案。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "批量拒绝失败，请逐项复审。");
    } finally {
      setBusyId("");
    }
  };
  return <section className="memory-proposal-inbox" aria-labelledby="memory-proposal-inbox-title"><div className="memory-proposal-inbox-head"><div><span className="eyebrow">REVIEW INBOX</span><h3 id="memory-proposal-inbox-title">自动分析提案</h3><p>摘要已经自动更新；人物、关系和情节线候选会先放在这里，确认后才进入正典。</p></div>{proposals.length > 0 && <div className="memory-proposal-inbox-actions"><span>{proposals.length} 条待处理</span><button type="button" className="text-button" onClick={() => void applyAll()} disabled={Boolean(busyId)}><CheckCircle2 size={12} /> 全部接受</button><button type="button" className="text-button text-danger" onClick={() => void rejectAll()} disabled={Boolean(busyId)}><X size={12} /> 全部拒绝</button></div>}</div>{proposalsQuery.isLoading ? <div className="memory-proposal-empty"><Loader2 size={15} className="spin" /> 正在读取提案收件箱…</div> : proposals.length === 0 ? <div className="memory-proposal-empty"><CheckCircle2 size={15} /> 暂无待处理的自动分析提案</div> : <div className="memory-proposal-list">{proposals.map((proposal) => <article className="memory-proposal-item" key={proposal.id}><div><strong>{proposal.summary}</strong><small>{proposal.operation || proposal.target_type || "结构化候选"} · {proposal.patches.length} 项变化</small></div><div className="memory-proposal-patches">{proposal.patches.slice(0, 4).map((patch, index) => <span key={`${patch.path}-${index}`}>{patch.label || patch.path}：{String(patch.value ?? "—")}</span>)}</div><div className="memory-proposal-item-actions"><button type="button" className="button button-primary button-small" onClick={() => void applyOne(proposal)} disabled={Boolean(busyId)}><Check size={12} /> 接受</button><button type="button" className="button button-secondary button-small" onClick={() => void rejectOne(proposal)} disabled={Boolean(busyId)}><X size={12} /> 拒绝</button></div></article>)}</div>}{notice && <p className="memory-proposal-notice" role="status">{notice}</p>}</section>;
}

function textHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
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
  onCharacterPatch,
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
  onCharacterPatch: (patch: AgentPatch) => void;
  onProposalApplied: (proposal: AssistantProposal) => void | Promise<void>;
  relationNodeCount?: number;
  mobileVisible?: boolean;
  autoOpen?: boolean;
  onNotice?: StoryStudioProps["onNotice"];
}) {
  const queryClient = useQueryClient();
  const [conversation, setConversation] = useState<AssistantConversation | null>(null);
  const [conversationTargetKey, setConversationTargetKey] = useState("");
  const [messages, setMessages] = useState<AssistantConversation["messages"]>([]);
  const [proposals, setProposals] = useState<AssistantProposal[]>([]);
  const [message, setMessage] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [status, setStatus] = useState<AssistantConversation["status"]>("idle");
  const [busyProposal, setBusyProposal] = useState("");
  const [notice, setNotice] = useState(autoOpen ? "Agent 已就位，可以从右侧开始描述你的想法。" : "");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [scopeMode, setScopeMode] = useState<"project" | "chapter" | "selection">(() => activeChapter ? "chapter" : "project");
  const [selectionText, setSelectionText] = useState("");
  const [selectionSnapshot, setSelectionSnapshot] = useState<AgentSelectionSnapshot | null>(null);
  const [allowImage, setAllowImage] = useState(false);
  const sequenceRef = useRef(0);
  const chapterPreviewRef = useRef<HTMLTextAreaElement>(null);
  const initializedHistory = useRef(false);
  const targetKey = `${target.type}:${target.id}`;
  useEffect(() => {
    if (!activeChapter && scopeMode !== "project") setScopeMode("project");
  }, [activeChapter, scopeMode]);
  const conversationsQuery = useQuery({
    queryKey: ["assistant-conversations", project.id],
    queryFn: () => listAssistantConversations(project.id),
    staleTime: 15_000,
  });
  const conversationRows = conversationsQuery.data || [];

  const loadConversation = useCallback(async (conversationId: string) => {
    try {
      const metadata = await getAssistantConversation(project.id, conversationId);
      const loadedMessages = await listAssistantMessages(project.id, conversationId);
      const inferredTarget = [...loadedMessages].reverse().find((item) => item.target)?.target || target;
      const loadedProposals = metadata.proposals?.length
        ? metadata.proposals
        : await listAssistantProposals(project.id, conversationId);
      const loaded = { ...metadata, target: inferredTarget, messages: loadedMessages, proposals: loadedProposals };
      setConversation(loaded);
      setConversationTargetKey(`${inferredTarget.type}:${inferredTarget.id}`);
      setMessages(loadedMessages);
      setProposals(loadedProposals);
      setStreamingText("");
      setStatus("idle");
      sequenceRef.current = 0;
    } catch (error) {
      notifyFallback(onNotice, "warning", error instanceof Error ? error.message : "历史会话读取失败。");
    }
  }, [onNotice, project.id, target]);

  useEffect(() => {
    if (initializedHistory.current || !conversationRows.length) return;
    initializedHistory.current = true;
    void loadConversation(conversationRows[0].id);
  }, [conversationRows, loadConversation]);

  useEffect(() => {
    if (!conversation?.id) return undefined;
    const conversationId = conversation.id;
    const cleanup = listenAssistantEvents(project.id, conversationId, (event: AssistantEvent) => {
      if (event.sequence && event.sequence <= sequenceRef.current) return;
      if (event.sequence) sequenceRef.current = event.sequence;
      if (event.type === "message_delta") {
        setStreamingText((current) => current + event.delta);
        setStatus("streaming");
      } else if (event.type === "message_completed") {
        setStreamingText("");
        setStatus("idle");
        void loadConversation(conversationId);
      } else if (event.type === "proposal_created") {
        setProposals((current) => [...current.filter((item) => item.id !== event.proposal.id), event.proposal]);
        void listAssistantProposals(project.id, conversationId).then((rows) => {
          const next = rows.find((item) => item.id === event.proposal.id);
          if (next) setProposals((current) => [...current.filter((item) => item.id !== next.id), next]);
        }).catch(() => undefined);
      } else if (event.type === "proposal_patch") {
        setProposals((current) => current.map((proposal) => proposal.id === event.proposal_id ? { ...proposal, patches: [...proposal.patches, event.patch] } : proposal));
        onCharacterPatch(event.patch);
      } else if (event.type === "proposal_completed") {
        setProposals((current) => current.map((proposal) => proposal.id === event.proposal_id ? { ...proposal, status: "proposed" } : proposal));
        setStatus("idle");
      } else if (event.type === "status") {
        setStatus(event.status);
        if (event.message) setNotice(event.message);
      } else if (event.type === "error") {
        setStatus("error");
        setNotice(event.message);
      }
    }, () => {
      setStatus("disconnected");
      setNotice("实时连接中断；重新发送消息即可继续同步。\n");
    }, sequenceRef.current);
    return cleanup;
  }, [conversation?.id, loadConversation, onCharacterPatch, project.id]);

  const startNewConversation = () => {
    setConversation(null);
    setConversationTargetKey("");
    setMessages([]);
    setProposals([]);
    setStreamingText("");
    setStatus("idle");
    setNotice("新对话已准备好。你可以描述人物、章节或选区需要怎样改变。");
    setHistoryOpen(false);
    sequenceRef.current = 0;
  };
  const ensureConversation = async () => {
    if (conversation && conversationTargetKey === targetKey) return conversation;
    const created = await createAssistantConversation(project.id, { target });
    const loaded = { ...created, target, messages: created.messages || [], proposals: created.proposals || [] };
    setConversation(loaded);
    setConversationTargetKey(targetKey);
    setMessages(loaded.messages);
    setProposals(loaded.proposals);
    queryClient.invalidateQueries({ queryKey: ["assistant-conversations", project.id] });
    return loaded;
  };
  const captureSelection = () => {
    const mountedEditor = Array.from(
      document.querySelectorAll<HTMLTextAreaElement>("textarea[data-chapter-id]"),
    ).find((item) => item.dataset.chapterId === activeChapter?.id);
    const editor = mountedEditor || chapterPreviewRef.current;
    const editorStart = editor?.selectionStart ?? 0;
    const editorEnd = editor?.selectionEnd ?? 0;
    const selected = editor && editorEnd > editorStart
      ? activeContent.slice(editorStart, editorEnd)
      : window.getSelection()?.toString() || "";
    if (!selected.trim()) {
      setNotice("请先在正文中选中一段连续文字，再读取选区。");
      return;
    }
    const start = editor && editorEnd > editorStart
      ? editorStart
      : activeContent.indexOf(selected);
    if (start < 0 || !activeChapter) {
      setNotice("这段文字不在当前章节，已停止发送；请重新读取当前稿纸选区。");
      return;
    }
    const snapshot: AgentSelectionSnapshot = {
      chapter_id: activeChapter.id,
      base_revision_id: activeChapter.revision_id || null,
      start,
      end: start + selected.length,
      hash: textHash(selected),
      quote: selected.slice(0, 240),
    };
    setSelectionText(selected);
    setSelectionSnapshot(snapshot);
    setScopeMode("selection");
    setNotice("已锁定当前选区；如果正文发生变化，需要重新读取后再生成。");
  };
  const currentSelection = selectionSnapshot && activeChapter && activeChapter.id === selectionSnapshot.chapter_id
    ? activeContent.slice(selectionSnapshot.start, selectionSnapshot.end)
    : "";
  const selectionStale = scopeMode === "selection" && (!selectionSnapshot || !activeChapter || activeChapter.id !== selectionSnapshot.chapter_id || textHash(currentSelection) !== selectionSnapshot.hash || (activeChapter.revision_id && selectionSnapshot.base_revision_id && activeChapter.revision_id !== selectionSnapshot.base_revision_id));
  const contextSnapshot = (): AgentContextSnapshot => {
    const base: AgentContextSnapshot = {
      chapter_id: scopeMode === "project" ? null : activeChapter?.id || null,
      base_revision_id: scopeMode === "project" ? null : activeChapter?.revision_id || null,
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
    if (!content || status === "streaming") return;
    if (!activeChapter && scopeMode !== "project") {
      setNotice("当前还没有章节；可以切换到全局设定，先和 Agent 搭建故事骨架。");
      return;
    }
    if (selectionStale) {
      setNotice("正文选区已变化，请重新读取选区后再生成，避免覆盖新内容。");
      return;
    }
    setMessage("");
    setNotice("");
    setStreamingText("");
    setStatus("streaming");
    const snapshot = contextSnapshot();
    const messageTarget: AgentTarget = scopeMode !== "project" && activeChapter
      ? { type: "chapter", id: activeChapter.id, chapter_id: activeChapter.id }
      : target.type === "project"
        ? { ...target, chapter_id: null }
        : target;
    const imageId = character?.image_media_id || character?.portrait?.id || "";
    const authorisedAssets = allowImage && imageId ? [imageId] : [];
    setAllowImage(false); // image permission is intentionally one-shot
    const localMessage = { id: `local-${Date.now()}`, role: "user" as const, content, created_at: new Date().toISOString(), proposal_ids: [], target: messageTarget, context_snapshot: snapshot, authorized_asset_ids: authorisedAssets };
    setMessages((current) => [...current, localMessage]);
    try {
      const active = await ensureConversation();
      await sendAssistantMessage(project.id, active.id, content, {
        target: messageTarget,
        context_snapshot: snapshot,
        authorized_asset_ids: authorisedAssets,
        expected_version: active.version,
      });
    } catch (error) {
      setStatus("error");
      setNotice(error instanceof Error ? error.message : "Agent 暂时不可用，请检查 Provider 配置。\n");
    }
  };
  const decide = async (proposal: AssistantProposal, action: "apply" | "reject") => {
    if (!conversation) return;
    setBusyProposal(proposal.id);
    try {
      const next = action === "apply"
        ? await applyAssistantProposal(project.id, conversation.id, proposal.id, {
            expected_version: proposal.base_version,
            expected_memory_epoch: proposal.base_memory_epoch,
          })
        : await rejectAssistantProposal(project.id, conversation.id, proposal.id);
      setProposals((current) => current.map((item) => item.id === next.id ? { ...item, ...next, patches: next.patches.length ? next.patches : item.patches } : item));
      if (action === "apply") {
        await onProposalApplied(next);
        if (next.target.type === "chapter" || proposal.target.type === "chapter") {
          await queryClient.invalidateQueries({ queryKey: ["chapters", project.id] });
          setNotice("正文提案已应用；请在章节中复审 diff，确认后再继续生成。");
        }
      } else {
        setNotice("提案已拒绝，正文和设定保持不变。");
      }
    } catch (error) {
      const code = apiErrorCode(error);
      notifyFallback(onNotice, "warning", code === "proposal_conflict" ? "正文或选区已经变化，无法安全应用；请重新读取选区并生成。" : error instanceof Error ? error.message : "提案状态更新失败。\n");
    } finally {
      setBusyProposal("");
    }
  };
  const targetLabel = target.type === "character" ? character?.name || "未命名人物" : target.type === "project" ? project.title : activeChapter?.title || "当前稿纸";
  const canSeeImage = Boolean(assistantProvider?.capabilities?.vision || assistantProvider?.capabilities?.image_input || assistantProvider?.capabilities?.multimodal);
  const quickPromptVisibility = getAgentQuickPromptVisibility(target, relationNodeCount);
  const bulkProposals = proposals.filter((proposal) => proposal.status === "proposed");
  const bulkActions: ProposalBulkActions | null = bulkProposals.length > 1
    ? { firstId: bulkProposals[0].id, ids: bulkProposals.map((proposal) => proposal.id), busy: Boolean(busyProposal), apply: () => void applyAllProposals(), reject: () => void rejectAllProposals() }
    : null;
  const applyAllProposals = async () => {
    if (!bulkProposals.length) return;
    setBusyProposal("bulk");
    try {
      const next = await applyAssistantProposals(project.id, bulkProposals.map((proposal) => proposal.id), {
        expected_memory_epoch: bulkProposals[0].base_memory_epoch,
        expected_versions: Object.fromEntries(
          bulkProposals
            .filter((proposal) => proposal.base_version != null)
            .map((proposal) => [proposal.id, proposal.base_version as number]),
        ),
      });
      const appliedById = new Map(next.map((proposal) => [proposal.id, proposal]));
      setProposals((current) => current.map((proposal) => {
        const applied = appliedById.get(proposal.id);
        return applied ? { ...proposal, ...applied, patches: applied.patches.length ? applied.patches : proposal.patches } : proposal;
      }));
      await Promise.all(next.map((proposal) => onProposalApplied(proposal)));
      await queryClient.invalidateQueries({ queryKey: ["chapters", project.id] });
      setNotice(`已接受 ${next.length || bulkProposals.length} 条提案；请复审正文差异。`);
    } catch (error) {
      const code = apiErrorCode(error);
      notifyFallback(onNotice, "warning", code === "proposal_conflict" ? "部分提案与当前正文或设定冲突，请逐项复审后重试。" : error instanceof Error ? error.message : "批量接受提案失败。");
    } finally {
      setBusyProposal("");
    }
  };
  const rejectAllProposals = async () => {
    if (!bulkProposals.length) return;
    setBusyProposal("bulk");
    try {
      const next = await rejectAssistantProposals(project.id, bulkProposals.map((proposal) => proposal.id));
      const rejectedById = new Map(next.map((proposal) => [proposal.id, proposal]));
      setProposals((current) => current.map((proposal) => rejectedById.has(proposal.id) ? { ...proposal, ...rejectedById.get(proposal.id), patches: proposal.patches } : proposal));
      setNotice(`已拒绝 ${next.length || bulkProposals.length} 条提案，正文和设定保持不变。`);
    } catch (error) {
      notifyFallback(onNotice, "warning", error instanceof Error ? error.message : "批量拒绝提案失败。");
    } finally {
      setBusyProposal("");
    }
  };
  return <aside className={`agent-dock ${mobileVisible ? "" : "mobile-panel-hidden"}`}><div className="agent-dock-head"><div><span className="eyebrow">STORY COMPANION</span><h2><Bot size={17} /> 和 Agent 一起填</h2></div><div className="agent-dock-head-actions"><span className={`agent-status-dot agent-status-${status}`} title={status} /><button type="button" className="quiet-icon" onClick={startNewConversation} aria-label="新建 Agent 对话" title="新对话"><Plus size={15} /></button></div></div><div className="agent-session-bar"><button type="button" className="text-button" onClick={() => setHistoryOpen((open) => !open)} aria-expanded={historyOpen}><MessageCircle size={13} /> {conversation ? "当前会话" : "恢复最近会话"} <small>{conversationRows.length ? `${conversationRows.length} 个历史` : "暂无历史"}</small></button><button type="button" className="text-button" onClick={startNewConversation}><Plus size={13} /> 新对话</button></div>{historyOpen && <div className="agent-history" role="listbox" aria-label="历史 Agent 会话">{conversationRows.length ? conversationRows.map((row) => <button type="button" role="option" aria-selected={row.id === conversation?.id} key={row.id} onClick={() => { setHistoryOpen(false); void loadConversation(row.id); }}><strong>{row.title || "故事设定助手"}</strong><small>{row.updated_at ? formatDate(row.updated_at) : "历史会话"}</small></button>) : <span>还没有持久会话</span>}</div>}<div className="agent-target"><span><UserRound size={13} /> 当前目标</span><strong>{targetLabel}</strong><small>{target.type === "character" ? "人物卷宗" : target.type === "project" ? "全局故事设定" : "当前稿纸"}</small></div><div className="agent-scope" aria-label="Agent 正文作用范围"><div className="agent-scope-head"><span><PencilLine size={13} /> 正文范围</span><small>{scopeMode === "project" ? "不限定章节 · 项目级设定" : activeChapter ? `${activeChapter.title} · 修订 ${activeChapter.revision_id ? activeChapter.revision_id.slice(0, 8) : "未保存"}` : "尚未选择章节"}</small></div><div className="agent-scope-tabs" role="group" aria-label="Agent 作用范围选择"><button type="button" className={scopeMode === "project" ? "is-active" : ""} onClick={() => setScopeMode("project")}>全局设定</button><button type="button" className={scopeMode === "chapter" ? "is-active" : ""} onClick={() => setScopeMode("chapter")} disabled={!activeChapter}>当前章节</button><button type="button" className={scopeMode === "selection" ? "is-active" : ""} onClick={() => setScopeMode("selection")} disabled={!activeChapter}>当前选区</button></div>{chapters.length > 0 && <select className="agent-chapter-select" aria-label="Agent 当前章节" value={activeChapter?.id || ""} onChange={(event) => { const chapter = chapters.find((item) => item.id === event.target.value); if (chapter) { onChapter(chapter); setSelectionSnapshot(null); setSelectionText(""); setScopeMode("chapter"); notifyFallback(onNotice, "info", `已切换 Agent 当前章节：${chapter.title}`); } }}><option value="">选择章节</option>{chapters.map((chapter) => <option value={chapter.id} key={chapter.id}>{String(chapter.number).padStart(2, "0")} · {chapter.title}</option>)}</select>}{scopeMode === "selection" && <div className="agent-selection-box"><textarea ref={chapterPreviewRef} className="agent-chapter-preview" value={activeContent} readOnly rows={5} aria-label="在当前章节预览中选择正文" /><button type="button" className="button button-secondary button-small" onClick={captureSelection}><PencilLine size={12} /> 锁定上方选区</button><textarea value={selectionText} onChange={(event) => { const text = event.target.value; setSelectionText(text); if (activeChapter && text) { const start = activeContent.indexOf(text); setSelectionSnapshot(start >= 0 ? { chapter_id: activeChapter.id, base_revision_id: activeChapter.revision_id || null, start, end: start + text.length, hash: textHash(text), quote: text.slice(0, 240) } : null); } }} placeholder="也可以粘贴当前章节中的连续片段" rows={2} aria-label="当前选区文字" />{selectionStale && <small className="agent-selection-warning"><CircleAlert size={12} /> 选区已变化，请重新读取后再发送</small>}</div>}</div>{character?.portrait && <div className="agent-image-auth"><label><input type="checkbox" checked={allowImage} onChange={(event) => setAllowImage(event.target.checked)} disabled={!canSeeImage} /> 让 Agent 看这张人物图（本次）</label><small>{canSeeImage ? `${assistantProvider?.name || "当前 Assistant Provider"} 支持视觉输入；发送后授权立即清空。` : `${assistantProvider?.name || "当前 Assistant Provider"} 未声明视觉输入，本次不能发送图片。`}</small></div>}<div className="agent-messages" aria-live="polite">{messages.length === 0 && !streamingText ? <div className="agent-empty"><MessageCircle size={18} /><strong>把脑海里的片段说出来</strong><p>例如：“她表面冷静，其实害怕再次失去家人。” Agent 会把它拆成可以编辑的字段。</p>{(quickPromptVisibility.motivation || quickPromptVisibility.tension) && <div className="agent-quick-prompts">{quickPromptVisibility.motivation && <button type="button" onClick={() => setMessage("帮我补齐这个人物的动机、目标和核心冲突")}>补齐人物动力</button>}{quickPromptVisibility.tension && <button type="button" onClick={() => setMessage("根据现有设定，提出三种更有张力的关系")}>增加关系张力</button>}</div>}</div> : <>{messages.map((item) => <div className={`agent-message agent-message-${item.role}`} key={item.id}><span>{item.role === "user" ? "你" : item.role === "assistant" ? "Agent" : "系统"}</span><p>{item.content}</p>{item.context_snapshot?.selection && <small className="agent-message-context">选区 {item.context_snapshot.selection.start}–{item.context_snapshot.selection.end} · {item.context_snapshot.selection.hash.slice(0, 8)}</small>}</div>)}{streamingText && <div className="agent-message agent-message-assistant"><span>Agent</span><p>{streamingText}<i className="agent-caret" /></p></div>}</>}</div>{proposals.length > 0 && <div className="agent-proposals"><div className="agent-section-label"><span>实时提案</span><small>应用前仍可复审 · 一次只处理一个范围</small></div>{proposals.map((proposal) => <AgentProposalCard key={proposal.id} proposal={proposal} bulk={bulkActions} busy={busyProposal === proposal.id} onApply={() => void decide(proposal, "apply")} onReject={() => void decide(proposal, "reject")} />)}</div>}{notice && <div className={`agent-notice agent-notice-${status === "error" ? "error" : "info"}`}><CircleAlert size={13} /><span>{notice}</span></div>}<div className="agent-compose"><textarea value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void send(); } }} placeholder="告诉 Agent 你想补充、修改或连接什么…" rows={3} aria-label="发送给 Agent 的消息" /><div><small>⌘ / Ctrl + Enter 发送</small><button type="button" className="button button-primary button-small" onClick={() => void send()} disabled={!message.trim() || status === "streaming"}><Send size={13} /> {status === "streaming" ? "生成中…" : "发送"}</button></div></div></aside>;
}

export default function StoryStudio({ project, storyMap, chapters, activeChapter, activeContent, assistantProvider, memoryRun, projectMemory, initialMode = "characters", autoOpenAgent = false, onBack, onChapter, onAnalyzeMemory, onRetryMemory, onNotice }: StoryStudioProps) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<StudioMode>(initialMode);
  const [entityView, setEntityView] = useState<EntityViewMode>(initialMode === "story-map" ? "graph" : "table");
  const [selectedCharacterId, setSelectedCharacterId] = useState<string | null>(null);
  const [editing, setEditing] = useState<CharacterCard | null>(null);
  const [isNewCharacter, setIsNewCharacter] = useState(false);
  const [expandedCharacter, setExpandedCharacter] = useState<CharacterCard | null>(null);
  const [changedPaths, setChangedPaths] = useState<Set<string>>(new Set());
  const [pendingPortrait, setPendingPortrait] = useState<File | null>(null);
  const [pendingPortraitUrl, setPendingPortraitUrl] = useState("");
  const charactersQuery = useQuery({ queryKey: ["characters", project.id], queryFn: () => getCharacters(project.id), enabled: mode === "characters" || mode === "story-map" });
  const graphQuery = useQuery({ queryKey: ["story-graph", project.id], queryFn: () => getStoryGraph(project.id), enabled: mode === "story-map" || (mode === "characters" && entityView === "graph"), retry: false });
  const characters = charactersQuery.data || [];
  const selectedCharacter = characters.find((character) => character.id === selectedCharacterId) || null;
  useEffect(() => {
    if (!selectedCharacterId && characters[0]) setSelectedCharacterId(characters[0].id);
    if (selectedCharacterId && !characters.some((character) => character.id === selectedCharacterId) && !isNewCharacter) setSelectedCharacterId(characters[0]?.id || null);
  }, [characters, isNewCharacter, selectedCharacterId]);
  useEffect(() => {
    if (!isNewCharacter) setEditing(selectedCharacter ? { ...selectedCharacter, aliases: [...selectedCharacter.aliases], tags: [...selectedCharacter.tags], custom_fields: { ...selectedCharacter.custom_fields } } : null);
  }, [isNewCharacter, selectedCharacter]);
  useEffect(() => () => { if (pendingPortraitUrl) URL.revokeObjectURL(pendingPortraitUrl); }, [pendingPortraitUrl]);
  useEffect(() => {
    const onMobilePanel = (event: Event) => {
      const panel = (event as CustomEvent<"agent" | "dossier" | "graph">).detail;
      if (panel === "graph") {
        setMode("story-map");
        setEntityView("graph");
      } else if (panel === "dossier" && mode === "story-map") {
        setMode("characters");
        setEntityView("table");
      }
    };
    window.addEventListener("story-studio-mobile-panel", onMobilePanel);
    return () => window.removeEventListener("story-studio-mobile-panel", onMobilePanel);
  }, [mode]);
  const saveCharacterMutation = useMutation({
    mutationFn: async () => {
      if (!editing) throw new Error("还没有选择人物");
      const saved = isNewCharacter ? await createCharacter(project.id, characterPayload(editing)) : await updateCharacter(project.id, editing.id, characterPayload(editing));
      if (pendingPortrait) {
        const portrait = await uploadCharacterPortrait(project.id, saved.id, pendingPortrait, `${saved.name}的人物肖像`);
        const refreshed = await getCharacter(saved.id);
        return { ...refreshed, portrait, image_media_id: portrait.id };
      }
      return saved;
    },
    onSuccess: (saved) => {
      queryClient.setQueryData<CharacterCard[]>(["characters", project.id], (current) => {
        const list = current || [];
        return isNewCharacter ? [...list, saved] : list.map((item) => item.id === saved.id ? saved : item);
      });
      setSelectedCharacterId(saved.id);
      setEditing({ ...saved, aliases: [...saved.aliases], tags: [...saved.tags], custom_fields: { ...saved.custom_fields } });
      setIsNewCharacter(false);
      setPendingPortrait(null);
      if (pendingPortraitUrl) URL.revokeObjectURL(pendingPortraitUrl);
      setPendingPortraitUrl("");
      setChangedPaths(new Set());
      notifyFallback(onNotice, "success", "人物卷宗已保存，当前设定已生效。\n");
    },
    onError: (error) => notifyFallback(onNotice, "error", error instanceof Error ? error.message : "人物卷宗保存失败。\n"),
  });
  const refreshProjectAfterProposal = useCallback(async (input?: AssistantProposal | AssistantProposal[]) => {
    const appliedProposals = Array.isArray(input) ? input : input ? [input] : [];
    try {
      const [latestProjects, latestCharacters, latestChapters, latestStoryMap, latestGraph] = await Promise.all([
        getProjects(),
        getCharacters(project.id),
        getChapters(project.id),
        getStoryMap(project.id),
        getStoryGraph(project.id),
      ]);
      queryClient.setQueryData(["projects"], latestProjects);
      queryClient.setQueryData(["characters", project.id], latestCharacters);
      queryClient.setQueryData(["chapters", project.id], latestChapters);
      queryClient.setQueryData(["story-map", project.id], latestStoryMap);
      queryClient.setQueryData(["story-graph", project.id], latestGraph);
      const targetProposal = appliedProposals.find((proposal) => proposal.target.type === "character") || appliedProposals[0];
      const targetId = targetProposal?.target_id || targetProposal?.target.id;
      const current = editing && editing.id !== "new-character"
        ? latestCharacters.find((item) => item.id === editing.id)
        : targetId && targetProposal?.target.type === "character"
          ? latestCharacters.find((item) => item.id === targetId)
          : undefined;
      if (current) {
        setSelectedCharacterId(current.id);
        setEditing({ ...current, aliases: [...current.aliases], tags: [...current.tags], custom_fields: { ...current.custom_fields } });
        setIsNewCharacter(false);
      }
    } catch (error) {
      notifyFallback(onNotice, "warning", error instanceof Error ? error.message : "提案已应用，但项目资料刷新失败，请稍后重试。");
    }
  }, [editing, onNotice, project.id, queryClient]);
  const handleCharacterPatch = useCallback((patch: AgentPatch) => {
    if (!editing || targetMode(mode, editing) !== "character") return;
    setEditing((current) => current ? applyCharacterPatch(current, patch) : current);
    setChangedPaths((current) => new Set(current).add(patchPath(patch.path)));
  }, [editing, mode]);
  const handleUpload = (file: File) => {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) { notifyFallback(onNotice, "warning", "请选择 JPG、PNG 或 WebP 图片。\n"); return; }
    if (file.size > 10 * 1024 * 1024) { notifyFallback(onNotice, "warning", "图片不能超过 10 MB。\n"); return; }
    if (!editing) return;
    const url = URL.createObjectURL(file);
    if (pendingPortraitUrl) URL.revokeObjectURL(pendingPortraitUrl);
    setPendingPortrait(file);
    setPendingPortraitUrl(url);
    setEditing({ ...editing, portrait: { id: "local-preview", project_id: project.id, url, filename: file.name, alt: `${editing.name || "人物"}人物肖像` } });
  };
  const removePortrait = async () => {
    if (!editing) return;
    if (pendingPortraitUrl) URL.revokeObjectURL(pendingPortraitUrl);
    setPendingPortrait(null);
    setPendingPortraitUrl("");
    if (editing.id !== "new-character" && editing.portrait?.id && editing.portrait.id !== "local-preview") {
      try {
        await deleteCharacterPortrait(project.id, editing.id, editing.image_media_id || editing.portrait.id);
        const refreshed = await getCharacter(editing.id);
        queryClient.setQueryData<CharacterCard[]>(["characters", project.id], (current) => (current || []).map((item) => item.id === refreshed.id ? refreshed : item));
        setEditing(refreshed);
        return;
      } catch (error) { notifyFallback(onNotice, "warning", error instanceof Error ? error.message : "头像移除失败。\n"); }
    }
    setEditing({ ...editing, portrait: null });
  };
  const newCharacter = () => {
    setMode("characters");
    setIsNewCharacter(true);
    setSelectedCharacterId("new-character");
    setEditing(emptyCharacter(project.id));
    setChangedPaths(new Set());
  };
  const storyGraphFallback = useMemo(() => fallbackGraph(project, characters, storyMap), [characters, project, storyMap]);
  const persistedGraph = graphQuery.data || storyMap.graph || { nodes: [], edges: [] };
  const graph = useMemo(() => mergeStoryGraphs(persistedGraph, storyGraphFallback), [persistedGraph, storyGraphFallback]);
  const target = useMemo<AgentTarget>(() => mode === "characters" && editing ? { type: "character", id: editing.id } : { type: "project", id: project.id }, [editing?.id, mode, project.id]);
  const overview = mode === "manuscript";
  const confirmedChapters = chapters.filter((chapter) => ["confirmed", "accepted", "published", "committed"].includes(chapter.status || ""));
  const memoryLabel = memoryRun?.status === "running" || memoryRun?.status === "queued"
    ? "正在整理故事记忆"
    : memoryRun?.status === "failed" || confirmedChapters.some((chapter) => chapter.summary_status === "failed")
      ? "故事记忆整理失败"
      : project.summary_status === "needs_review" || project.summary_status === "stale" || confirmedChapters.some((chapter) => chapter.summary_status === "needs_review" || chapter.summary_status === "stale")
        ? "摘要待复核"
        : confirmedChapters.length === 0
          ? "完成第一章后建立记忆"
          : confirmedChapters.some((chapter) => !chapter.summary || chapter.summary_status !== "current")
            ? "故事记忆尚未初始化"
            : "故事记忆已就绪";
  const memoryDotActive = memoryLabel === "故事记忆已就绪" || memoryLabel === "正在整理故事记忆";
  return <LayoutGroup><div className="studio-page"><header className="studio-header"><div className="studio-header-left"><button className="back-to-library" onClick={onBack}><ArrowLeft size={15} /> 返回工作台</button><div className="studio-project-title"><span className="eyebrow">SETTING WORKSHOP / {project.title}</span><h1>设定工坊</h1></div></div><div className="studio-header-actions"><span className="studio-memory-state"><span className={`status-dot ${memoryDotActive ? "green" : ""}`} /> {memoryLabel}</span><button className="button button-secondary button-small" onClick={memoryRun?.status === "failed" || memoryRun?.status === "stale" || memoryRun?.status === "needs_retry" ? onRetryMemory || onAnalyzeMemory : onAnalyzeMemory} disabled={memoryRun?.status === "running" || memoryRun?.status === "queued"}><RefreshCw size={13} /> {memoryRun?.status === "failed" || memoryRun?.status === "stale" || memoryRun?.status === "needs_retry" ? "重试整理" : memoryRun?.status === "running" || memoryRun?.status === "queued" ? "整理中…" : "整理本书"}</button></div></header><div className="studio-layout"><aside className="studio-sidebar"><div className="studio-nav-label"><span>故事卷宗</span><small>{characters.length} 位人物</small></div><nav className="studio-nav" aria-label="设定工坊分类"><button className={overview ? "is-active" : ""} onClick={() => setMode("manuscript")}><LayoutGrid size={15} /> 故事总览</button><button className={mode === "characters" ? "is-active" : ""} onClick={() => setMode("characters")}><UserRound size={15} /> 人物卷宗 <small>{characters.length}</small></button><button className={mode === "story-map" ? "is-active" : ""} onClick={() => { setMode("story-map"); setEntityView("graph"); }}><Network size={15} /> 故事图谱</button></nav><StudioChapterList chapters={chapters} activeChapterId={activeChapter?.id} onChapter={onChapter} />{mode === "characters" && <div className="studio-character-list"><div className="studio-character-list-head"><span>人物索引</span><button className="quiet-icon" onClick={newCharacter} aria-label="新增人物"><Plus size={15} /></button></div>{characters.map((character) => <button className={`studio-character-list-item ${character.id === selectedCharacterId ? "is-selected" : ""}`} key={character.id} onClick={() => { setIsNewCharacter(false); setSelectedCharacterId(character.id); }}><CharacterPortrait character={character} /><span><strong>{character.name || "未命名人物"}</strong><small>{character.role || "待补身份"}</small></span><span className={`mini-status status-${character.status}`}>{character.status === "confirmed" ? "已入典" : character.status === "needs_review" ? "待复核" : "草稿"}</span></button>)}{characters.length === 0 && <div className="studio-list-empty"><UserRound size={16} />还没有人物<br /><small>可手动新增，或请 Agent 先搭一张卷宗。</small></div>}</div>}</aside><main className="studio-main">{overview ? <StoryOverview project={project} storyMap={storyMap} memoryRun={memoryRun} projectMemory={projectMemory} onAnalyze={onAnalyzeMemory} onProposalsChanged={refreshProjectAfterProposal} /> : mode === "story-map" ? <StoryGraphView projectId={project.id} graph={graph} fallback={storyGraphFallback} onNotice={onNotice} /> : entityView === "graph" ? <StoryGraphView projectId={project.id} graph={graph} fallback={storyGraphFallback} onNotice={onNotice} /> : <div className="character-workbench"><div className="character-workbench-head"><div><span className="eyebrow">CHARACTER ARCHIVE</span><h2>{isNewCharacter ? "新增人物卷宗" : editing?.name || "选择一位人物"}</h2><p>左侧选人物，中间按表格编辑；右侧 Agent 会把对话实时落到字段里。</p></div><div className="view-switch" role="group" aria-label="人物观察方式"><button className={entityView === "table" ? "is-active" : ""} onClick={() => setEntityView("table")}><Table2 size={14} /> 表格</button><button className={(entityView as EntityViewMode) === "graph" ? "is-active" : ""} onClick={() => { setEntityView("graph"); setMode("characters"); }}><Network size={14} /> 关系图</button></div></div><CharacterGallery characters={characters} onOpen={setExpandedCharacter} onCreate={newCharacter} />{editing ? <CharacterForm character={editing} onChange={setEditing} onSave={() => void saveCharacterMutation.mutate()} onUpload={handleUpload} onRemovePortrait={() => void removePortrait()} busy={saveCharacterMutation.isPending} changedPaths={changedPaths} /> : <div className="studio-empty"><UserRound size={22} /><strong>选择或新增一位人物</strong><p>每本小说都有独立的人物卷宗；手动填写和 Agent 填写会共用同一张表。</p><button className="button button-primary" onClick={newCharacter}><Plus size={14} /> 新增人物</button></div>}</div>}</main><AgentDock project={project} target={target} character={editing} relationNodeCount={graph.nodes.length} chapters={chapters} activeChapter={activeChapter} activeContent={activeContent} onChapter={onChapter} assistantProvider={assistantProvider} onCharacterPatch={handleCharacterPatch} onProposalApplied={async (proposal) => { setChangedPaths((current) => new Set([...current, ...proposal.patches.map((patch) => patchPath(patch.path))])); await refreshProjectAfterProposal(proposal); }} autoOpen={autoOpenAgent} onNotice={onNotice} /></div>{expandedCharacter && <CharacterDetailOverlay character={expandedCharacter} onClose={() => setExpandedCharacter(null)} />}</div></LayoutGroup>;
}

function StudioChapterList({ chapters, activeChapterId, onChapter }: { chapters: Chapter[]; activeChapterId?: string | null; onChapter: (chapter: Chapter) => void }) {
  return <section className="studio-chapter-list" aria-labelledby="studio-chapter-list-title"><div className="studio-chapter-list-head"><span id="studio-chapter-list-title">稿纸入口</span><small>{chapters.length} 张</small></div>{chapters.length ? chapters.map((chapter) => <button type="button" className={`studio-chapter-item ${chapter.id === activeChapterId ? "is-selected" : ""}`} key={chapter.id} onClick={() => onChapter(chapter)}><span className="studio-chapter-number">{String(chapter.number).padStart(2, "0")}</span><span><strong>{chapter.title || "未命名稿纸"}</strong><small>{chapter.summary ? chapter.summary.slice(0, 28) : chapter.summary_status === "running" ? "摘要整理中…" : "尚未整理摘要"}</small></span><span className={`studio-chapter-status chapter-${chapter.status || "draft"}`} title={chapter.status || "draft"}>{chapter.status === "generating" ? <Loader2 size={12} className="spin" /> : chapter.status === "failed" || chapter.status === "rejected" ? <CircleAlert size={12} /> : chapter.status === "accepted" || chapter.status === "confirmed" ? <Check size={12} /> : <PencilLine size={12} />}</span></button>) : <p className="studio-list-empty">还没有稿纸；回到工作台新建一张空白稿纸。</p>}</section>;
}

function targetMode(mode: StudioMode, character: CharacterCard): AgentTarget["type"] {
  return mode === "characters" && character.id ? "character" : "project";
}

function StoryOverview({ project: projectInput, storyMap, memoryRun, projectMemory, onAnalyze, onProposalsChanged }: { project: Project; storyMap: StoryMap; memoryRun: MemoryRun | null; projectMemory?: ProjectMemory | null; onAnalyze: () => void; onProposalsChanged: (proposals: AssistantProposal[]) => void | Promise<void> }) {
  const threads = storyMap.threads || [];
  const timeline = storyMap.timeline || [];
  const characters = storyMap.characters || [];
  const projectSummary = projectMemory?.project_summary;
  const project = projectInput;
  const summaryText = projectSummary?.summary_text || "章节被确认后，故事摘要、人物关系与情节线会在这里形成。也可以手动整理一遍全书。";
  return <div className="story-overview"><div className="story-overview-hero"><div><span className="eyebrow">THE STORY BIBLE</span><h2>{project.title}</h2><p>{project.logline || "还没有一句话梗概。可以在右侧告诉 Agent：这本故事最想留下什么。"}</p></div><div className="story-overview-seal"><Sparkles size={19} /><span>记忆<br />卷</span></div></div><div className="story-overview-grid"><section className="overview-card overview-summary-card"><div className="overview-card-head"><div><span className="eyebrow">CURRENT MEMORY</span><h3>故事摘要</h3></div><span className={`overview-status ${memoryRun?.status === "running" || memoryRun?.status === "queued" ? "is-running" : ""}`}>{memoryRun?.status === "running" || memoryRun?.status === "queued" ? "整理中" : projectSummary?.summary_text && projectSummary.status === "current" ? "已整理" : projectSummary ? "待复核" : "待建立"}</span></div><p>{summaryText}</p><div className="overview-card-actions"><button className="button button-secondary button-small" onClick={onAnalyze} disabled={memoryRun?.status === "running" || memoryRun?.status === "queued"}><RefreshCw size={13} /> {memoryRun?.status === "running" || memoryRun?.status === "queued" ? "整理中…" : "分析全书"}</button>{projectSummary?.summary_text && projectSummary.status === "current" ? <span className="memory-applied-label"><Check size={13} /> 已自动更新</span> : null}</div></section><section className="overview-stat-card"><span className="overview-stat-icon"><UserRound size={15} /></span><strong>{characters.length}</strong><span>人物条目</span><small>可继续补全人物弧</small></section><section className="overview-stat-card"><span className="overview-stat-icon"><Network size={15} /></span><strong>{threads.length}</strong><span>剧情线</span><small>在关系图里连接主线</small></section><section className="overview-stat-card"><span className="overview-stat-icon"><Clock3 size={15} /></span><strong>{timeline.length}</strong><span>时间节点</span><small>让前后发生顺序可见</small></section></div><section className="overview-lanes"><div className="overview-section-head"><div><span className="eyebrow">PLOT THREADS</span><h3>正在发生的线索</h3></div><span>从图谱中继续编辑</span></div>{threads.length ? threads.slice(0, 5).map((thread) => <div className="overview-lane" key={thread.id}><span className="thread-color" style={{ background: thread.color || "#4F756B" }} /><div><strong>{thread.title}</strong><p>{thread.next_beat || thread.status || "等待下一拍"}</p></div><span className={`thread-status thread-${thread.status}`}>{thread.status === "active" ? "进行中" : thread.status || "待定"}</span></div>) : <div className="overview-empty"><Network size={16} /> 暂无剧情线；完成一次故事整理后会显示在这里。</div>}</section><MemoryProposalInbox projectId={project.id} memoryEpoch={projectMemory?.memory_epoch ?? project.memory_epoch} onChanged={onProposalsChanged} /></div>;
}
