import { describe, expect, it } from "vitest";
import {
  chapterAgentDraft,
  getAgentQuickPromptVisibility,
  graphWithAgentDrafts,
  resolveAgentMessageTarget,
  shouldAutoCreateChapterDraft,
  spreadOverlappingGraphNodes,
  summarizeAgentBuild,
  visibleAgentMessage,
} from "../src/StoryStudio";
import type { AssistantProposal, StoryGraph } from "../src/types";

describe("Agent message presentation", () => {
  it("keeps a structured JSON reply out of the transcript", () => {
    expect(
      visibleAgentMessage(
        '{"reply":"已整理人物设定。","proposals":[{"operation":"update_character"}]}',
      ),
    ).toBe("已整理人物设定。");
  });

  it("removes a legacy proposals tail while keeping natural language", () => {
    expect(
      visibleAgentMessage(
        "已整理人物设定。\n\nproposals:\n- operation: update_character\n  target_type: character",
      ),
    ).toBe("已整理人物设定。");
  });

  it("also removes the legacy protocol introduction line", () => {
    expect(
      visibleAgentMessage(
        "已整理人物设定。\n如果需要提交变更，可返回以下结构化申请：\nproposals:\n- operation: update_character",
      ),
    ).toBe("已整理人物设定。");
  });

  it("leaves ordinary assistant text unchanged", () => {
    expect(visibleAgentMessage("她终于走进雾里。")).toBe("她终于走进雾里。");
  });

  it("builds a read-only chapter draft without mutating the source text", () => {
    const draft = chapterAgentDraft(
      { id: "chapter-1" },
      "雾从海面漫上来。",
      [
        {
          id: "proposal-1",
          conversation_id: "conversation-1",
          target: { type: "chapter", id: "chapter-1" },
          target_type: "chapter",
          target_id: "chapter-1",
          operation: "edit_chapter",
          summary: "补一段开场",
          patches: [
            {
              path: "replacement",
              value: "雾从海面漫上来，灯塔在远处亮了一下。",
            },
            { path: "selection_start", value: 0 },
            { path: "selection_end", value: 8 },
          ],
          status: "proposed",
        },
      ],
    );

    expect(draft).toMatchObject({
      proposalId: "proposal-1",
      before: "雾从海面漫上来。",
      after: "雾从海面漫上来，灯塔在远处亮了一下。",
      replacement: "雾从海面漫上来，灯塔在远处亮了一下。",
      start: 0,
      end: 8,
    });
  });
});

describe("Agent empty-state shortcuts", () => {
  it("only offers character motivation for a persisted character target", () => {
    expect(
      getAgentQuickPromptVisibility({ type: "project", id: "project-1" }, 3),
    ).toEqual({ motivation: false, tension: true });
    expect(
      getAgentQuickPromptVisibility({ type: "character", id: "new-character" }, 1),
    ).toEqual({ motivation: false, tension: false });
    expect(
      getAgentQuickPromptVisibility({ type: "character", id: "character-1" }, 1),
    ).toEqual({ motivation: true, tension: false });
  });

  it("only offers relationship tension when the graph has two nodes", () => {
    expect(
      getAgentQuickPromptVisibility({ type: "project", id: "project-1" }, 0).tension,
    ).toBe(false);
    expect(
      getAgentQuickPromptVisibility({ type: "project", id: "project-1" }, 1).tension,
    ).toBe(false);
    expect(
      getAgentQuickPromptVisibility({ type: "project", id: "project-1" }, 2).tension,
    ).toBe(true);
  });
});

describe("resolveAgentMessageTarget", () => {
  const chapter = { id: "chapter-1" };

  it("uses the current chapter for the writing surface", () => {
    expect(
      resolveAgentMessageTarget(
        { type: "project", id: "project-1" },
        "chapter",
        chapter,
      ),
    ).toEqual({ type: "chapter", id: "chapter-1", chapter_id: "chapter-1" });
  });

  it("keeps the project target in whole-book collaboration mode", () => {
    expect(
      resolveAgentMessageTarget(
        { type: "project", id: "project-1" },
        "global",
        chapter,
      ),
    ).toEqual({ type: "project", id: "project-1", chapter_id: null });
  });

  it("keeps a character target above the chapter scope", () => {
    expect(
      resolveAgentMessageTarget(
        { type: "character", id: "character-1" },
        "chapter",
        chapter,
      ),
    ).toEqual({
      type: "character",
      id: "character-1",
      chapter_id: "chapter-1",
    });
  });

  it("keeps a graph relationship target in chapter mode", () => {
    expect(
      resolveAgentMessageTarget(
        { type: "relationship", id: "edge-1" },
        "chapter",
        chapter,
      ),
    ).toEqual({
      type: "relationship",
      id: "edge-1",
      chapter_id: "chapter-1",
    });
  });
});

describe("automatic Agent manuscript drafts", () => {
  it("creates a manuscript only for explicit chapter-writing requests", () => {
    expect(shouldAutoCreateChapterDraft("给我仿照这个风格写第一章"))
      .toBe(true);
    expect(shouldAutoCreateChapterDraft("续写这一章的正文"))
      .toBe(true);
    expect(shouldAutoCreateChapterDraft("以我的二十个六岁女房客为主题创造第一章"))
      .toBe(true);
    expect(shouldAutoCreateChapterDraft("撰写序章"))
      .toBe(true);
    expect(shouldAutoCreateChapterDraft("帮我分析第一章的节奏"))
      .toBe(false);
    expect(shouldAutoCreateChapterDraft("先设计两个人物"))
      .toBe(false);
  });
});

describe("Agent graph preview", () => {
  it("reflows an overlapping graph into a spacious adaptive grid", () => {
    const nodes = spreadOverlappingGraphNodes([
      {
        id: "node-1",
        type: "character",
        label: "甲",
        position: { x: 0, y: 0 },
      },
      {
        id: "node-2",
        type: "character",
        label: "乙",
        position: { x: 0, y: 0 },
      },
      {
        id: "node-3",
        type: "event",
        label: "远处事件",
        position: { x: 900, y: 500 },
      },
    ]);

    expect(nodes.map((node) => node.position)).toEqual([
      { x: 80, y: 80 },
      { x: 470, y: 80 },
      { x: 80, y: 270 },
    ]);
  });

  it("preserves a manually spaced layout", () => {
    const nodes = spreadOverlappingGraphNodes([
      {
        id: "node-1",
        type: "character",
        label: "甲",
        position: { x: 20, y: 30 },
      },
      {
        id: "node-2",
        type: "event",
        label: "远处事件",
        position: { x: 700, y: 400 },
      },
    ]);

    expect(nodes.map((node) => node.position)).toEqual([
      { x: 20, y: 30 },
      { x: 700, y: 400 },
    ]);
  });

  it("lays out a generated relationship chain in readable layers", () => {
    const nodes = spreadOverlappingGraphNodes(
      [
        { id: "a", type: "character", label: "甲", position: { x: 0, y: 0 } },
        { id: "b", type: "character", label: "乙", position: { x: 80, y: 0 } },
        { id: "c", type: "event", label: "事件", position: { x: 160, y: 0 } },
      ],
      [
        { id: "ab", source: "a", target: "b", kind: "related" },
        { id: "bc", source: "b", target: "c", kind: "related" },
        { id: "ac", source: "a", target: "c", kind: "related" },
      ],
    );

    expect(nodes[1].position.x - nodes[0].position.x).toBeGreaterThanOrEqual(390);
    expect(nodes[2].position.x - nodes[1].position.x).toBeGreaterThanOrEqual(390);
    expect(nodes[1].position.y).not.toBe(nodes[0].position.y);
    expect(nodes[2].position.y).toBe(nodes[0].position.y);
  });

  it("resolves a newest-first relation after its draft characters", () => {
    const proposals: AssistantProposal[] = [
      {
        id: "relation-1",
        conversation_id: "conversation-1",
        target: { type: "relationship", id: "" },
        target_type: "character_relation",
        operation: "upsert_graph_edge",
        summary: "建立彼此利用关系",
        patches: [
          { path: "source_name", value: "阿芜" },
          { path: "target_name", value: "季衡" },
          { path: "relation_type", value: "彼此利用" },
        ],
        status: "proposed",
      },
      ...["季衡", "阿芜"].map(
        (name, index): AssistantProposal => ({
          id: `character-${index}`,
          conversation_id: "conversation-1",
          target: { type: "character", id: "" },
          target_type: "character",
          operation: "create_character",
          summary: `新增${name}`,
          patches: [{ path: "name", value: name }],
          status: "proposed",
        }),
      ),
    ];
    const graph = graphWithAgentDrafts(
      { nodes: [], edges: [] } satisfies StoryGraph,
      proposals,
    );

    expect(graph.nodes.map((node) => node.label)).toEqual(["季衡", "阿芜"]);
    expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0]).toMatchObject({
      label: "彼此利用",
      data: { agentDraft: true, proposalId: "relation-1" },
    });
  });

  it("keeps a relation visible while its endpoint characters are generated", () => {
    const relation: AssistantProposal = {
      id: "relation-orphaned",
      conversation_id: "conversation-1",
      target: { type: "relationship", id: "" },
      target_type: "character_relation",
      operation: "upsert_graph_edge",
      summary: "仍需处理的关系",
      patches: [
        { path: "source_name", value: "林砚" },
        { path: "target_name", value: "苏晚" },
        { path: "relation_type", value: "情感牵绊" },
      ],
      status: "proposed",
    };

    const graph = graphWithAgentDrafts(
      { nodes: [], edges: [] } satisfies StoryGraph,
      [relation],
    );

    expect(graph.nodes.map((node) => node.label)).toEqual(["林砚", "苏晚"]);
    expect(
      graph.nodes.every(
        (node) => node.data?.agentDraft && node.data?.agentEndpointDraft,
      ),
    ).toBe(true);
    expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0].data?.proposalId).toBe("relation-orphaned");
  });

  it("summarizes a live multi-character build across cards and graph", () => {
    const proposals: AssistantProposal[] = [
      ...["季衡", "阿芜"].map(
        (name, index): AssistantProposal => ({
          id: `live-character-${index}`,
          conversation_id: "conversation-1",
          target: { type: "character", id: "" },
          target_type: "character",
          operation: "create_character",
          summary: `新增${name}`,
          patches: [{ path: "name", value: name }],
          status: index === 0 ? "building" : "proposed",
        }),
      ),
      {
        id: "live-relation",
        conversation_id: "conversation-1",
        target: { type: "relationship", id: "" },
        target_type: "relationship",
        operation: "upsert_graph_edge",
        summary: "建立关系",
        patches: [
          { path: "source_name", value: "季衡" },
          { path: "target_name", value: "阿芜" },
          { path: "relation_type", value: "互相试探" },
        ],
        status: "building",
      },
    ];
    const graph = graphWithAgentDrafts(
      { nodes: [], edges: [] } satisfies StoryGraph,
      proposals,
    );

    expect(summarizeAgentBuild(proposals, graph)).toEqual({
      total: 3,
      readyCount: 1,
      building: true,
      chapterCount: 0,
      characterCount: 2,
      graphProposalCount: 1,
      nodeCount: 2,
      edgeCount: 1,
      patchCount: 5,
    });
  });
});
