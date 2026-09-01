import { describe, expect, it } from "vitest";
import { getAgentQuickPromptVisibility } from "../src/StoryStudio";

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
