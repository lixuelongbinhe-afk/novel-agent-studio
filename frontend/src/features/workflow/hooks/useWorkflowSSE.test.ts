import { createElement, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type WorkflowRunEvent } from "../../../api/client";
import {
  MAX_RETAINED_WORKFLOW_EVENTS,
  mergeWorkflowEvents,
  useWorkflowSSE,
  workflowReconnectDelay
} from "./useWorkflowSSE";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client, children });
}

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

  it("stops immediately and exposes a permanent authorization failure", async () => {
    const stream = vi.spyOn(api, "streamWorkflowEvents").mockRejectedValue(
      new ApiError(401, "本地 API 鉴权失败", null)
    );
    const { result } = renderHook(
      () => useWorkflowSSE({ runId: 9, projectId: 1, active: true, snapshotEvents: [] }),
      { wrapper }
    );

    await waitFor(() => expect(result.current.error).toContain("连接已停止"));
    expect(stream).toHaveBeenCalledTimes(1);
  });

  it("retries transient failures with exponential delays", async () => {
    vi.useFakeTimers();
    const stream = vi.spyOn(api, "streamWorkflowEvents").mockRejectedValue(
      new TypeError("network disconnected")
    );
    const { unmount } = renderHook(
      () => useWorkflowSSE({ runId: 9, projectId: 1, active: true, snapshotEvents: [] }),
      { wrapper }
    );
    await act(async () => undefined);
    expect(stream).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(workflowReconnectDelay(0) - 1));
    expect(stream).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(stream).toHaveBeenCalledTimes(2);

    await act(async () => vi.advanceTimersByTimeAsync(workflowReconnectDelay(1) - 1));
    expect(stream).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(stream).toHaveBeenCalledTimes(3);
    unmount();
  });
});
