import { describe, expect, it } from "vitest";
import {
  chapterAgentDraft,
  getAgentQuickPromptVisibility,
  graphWithAgentDrafts,
  resolveAgentMessageTarget,
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

  it("keeps the project target when global settings is selected", () => {
    expect(
      resolveAgentMessageTarget(
        { type: "project", id: "project-1" },
        "project",
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
    ).toEqual({ type: "character", id: "character-1" });
  });

  it("keeps a graph relationship target above the selection scope", () => {
    expect(
      resolveAgentMessageTarget(
        { type: "relationship", id: "edge-1" },
        "selection",
        chapter,
      ),
    ).toEqual({ type: "relationship", id: "edge-1" });
  });
});

describe("Agent graph preview", () => {
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

  it("keeps a relation reviewable after its character drafts are gone", () => {
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
});
