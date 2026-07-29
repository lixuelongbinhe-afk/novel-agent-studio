import { describe, expect, it } from "vitest";

import type { WorkflowRunEvent } from "../../../api/client";
import {
  MAX_RETAINED_WORKFLOW_EVENTS,
  mergeWorkflowEvents
} from "./useWorkflowSSE";

function workflowEvent(sequence: number): WorkflowRunEvent {
  return {
    sequence,
    event: "node_output_delta",
    node_key: "writer",
    payload: { delta: String(sequence) },
    created_at: "2026-07-29T00:00:00Z"
  };
}

describe("mergeWorkflowEvents", () => {
  it("deduplicates, sorts, and retains only the newest bounded window", () => {
    const first = Array.from(
      { length: MAX_RETAINED_WORKFLOW_EVENTS },
      (_, index) => workflowEvent(index + 1)
    );
    const second = [
      workflowEvent(250),
      ...Array.from({ length: 300 }, (_, index) => workflowEvent(index + 501))
    ];

    const merged = mergeWorkflowEvents(
      first,
      second,
      MAX_RETAINED_WORKFLOW_EVENTS
    );

    expect(merged).toHaveLength(MAX_RETAINED_WORKFLOW_EVENTS);
    expect(merged[0]?.sequence).toBe(301);
    expect(merged[merged.length - 1]?.sequence).toBe(800);
    expect(new Set(merged.map((event) => event.sequence)).size).toBe(
      MAX_RETAINED_WORKFLOW_EVENTS
    );
  });
});
