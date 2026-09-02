import { describe, expect, it } from "vitest";

import {
  normalizeAssistantEvent,
  normalizeAssistantRun,
} from "../src/api";

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
