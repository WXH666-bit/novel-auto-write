import { describe, expect, it } from "vitest";
import {
  getAgentQuickPromptVisibility,
  resolveAgentMessageTarget,
} from "../src/StoryStudio";

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
