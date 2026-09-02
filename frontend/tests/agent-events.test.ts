import { describe, expect, it } from "vitest";

import {
  normalizeAssistantEvent,
  normalizeAssistantRun,
} from "../src/api";
import { classifyLiveAgentEvent } from "../src/StoryStudio";

describe("assistant event protocol", () => {
  it("keeps running for run-stage events and run snapshots", () => {
    expect(
      normalizeAssistantEvent({
        sequence: 3,
        event_type: "run.stage",
        payload_json: { status: "running", stage: "drafting" },
      }),
    ).toMatchObject({
      type: "status",
      status: "running",
      stage: "drafting",
    });

    expect(
      normalizeAssistantRun({
        id: "run-1",
        status: "running",
        stage: "drafting",
      }),
    ).toMatchObject({ id: "run-1", status: "running", stage: "drafting" });
  });

  it("does not turn a completed reply into a second status notice", () => {
    const event = normalizeAssistantEvent({
      sequence: 9,
      event_type: "run.completed",
      payload_json: {
        status: "completed",
        reply: "已整理人物设定。",
      },
    });

    expect(event).toMatchObject({ type: "status", status: "idle" });
    expect(event.type).toBe("status");
    if (event.type !== "status") throw new Error("expected status event");
    expect(event.message).toBeUndefined();
  });

  it("only follows proposals emitted after this send's cursor and run", () => {
    const pending = {
      conversationId: "conversation-1",
      baselineSequence: 20,
      runId: "run-live",
    };
    expect(
      classifyLiveAgentEvent(
        { sequence: 19, run_id: "run-live" },
        "conversation-1",
        pending,
        new Set(["run-old"]),
      ),
    ).toBe("stale");
    expect(
      classifyLiveAgentEvent(
        { sequence: 21, run_id: "run-old" },
        "conversation-1",
        pending,
        new Set(["run-old"]),
      ),
    ).toBe("stale");
    expect(
      classifyLiveAgentEvent(
        { sequence: 21, run_id: "run-live" },
        "conversation-1",
        pending,
        new Set(["run-old"]),
      ),
    ).toBe("current");
  });

  it("buffers legacy proposal frames that omit run_id", () => {
    expect(
      classifyLiveAgentEvent(
        { sequence: 21 },
        "conversation-1",
        {
          conversationId: "conversation-1",
          baselineSequence: 20,
          runId: "",
        },
        new Set(["run-old"]),
      ),
    ).toBe("awaiting-run");
  });

  it("keeps character relations as graph targets instead of character cards", () => {
    const event = normalizeAssistantEvent({
      sequence: 10,
      event_type: "proposal.created",
      payload_json: {
        operation: "upsert_graph_edge",
        proposal: {
          id: "proposal-edge",
          conversation_id: "conversation-1",
          target: { type: "character_relation", id: "" },
          patches: [],
          status: "building",
        },
      },
    });

    expect(event.type).toBe("proposal_created");
    if (event.type === "proposal_created") {
      expect(event.proposal.target.type).toBe("relationship");
      expect(event.proposal.operation).toBe("upsert_graph_edge");
      expect(event.proposal.status).toBe("building");
    }
  });

  it("keeps the durable run id on proposal events used for live following", () => {
    const event = normalizeAssistantEvent({
      sequence: 12,
      run_id: "run-live",
      event_type: "proposal.patch",
      payload_json: {
        proposal_id: "proposal-1",
        patch: { path: "name", value: "青萝" },
      },
    });

    expect(event.run_id).toBe("run-live");
  });

  it("inherits outer metadata when proposal.created nests a partial proposal", () => {
    const event = normalizeAssistantEvent({
      sequence: 8,
      event_type: "proposal.created",
      run_id: "run-1",
      conversation_id: "conversation-1",
      operation: "upsert_graph_node",
      target: { type: "thread", id: "thread-1" },
      target_type: "thread",
      target_id: "thread-1",
      base_version: 4,
      change_set_id: "change-1",
      created_at: "2026-09-02T00:00:00.000Z",
      payload_json: {
        proposal: {
          id: "proposal-1",
          patches: [{ path: "label", value: "新的线索" }],
        },
      },
    });

    expect(event).toMatchObject({ type: "proposal_created", run_id: "run-1" });
    if (event.type !== "proposal_created") throw new Error("expected proposal event");
    expect(event.proposal).toMatchObject({
      id: "proposal-1",
      conversation_id: "conversation-1",
      operation: "upsert_graph_node",
      target: { type: "thread", id: "thread-1" },
      target_type: "thread",
      target_id: "thread-1",
      base_version: 4,
      change_set_id: "change-1",
      created_at: "2026-09-02T00:00:00.000Z",
      patches: [{ path: "label", value: "新的线索" }],
    });
  });
});
