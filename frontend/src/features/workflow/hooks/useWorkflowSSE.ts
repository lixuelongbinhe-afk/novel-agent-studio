import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api, type WorkflowRunEvent } from "../../../api/client";

type WorkflowSSEOptions = {
  runId: number | null;
  projectId: number;
  active: boolean;
  snapshotEvents: WorkflowRunEvent[];
};

export const MAX_RETAINED_WORKFLOW_EVENTS = 500;
const MAX_RECONNECT_DELAY_MS = 8_000;

export function useWorkflowSSE({
  runId,
  projectId,
  active,
  snapshotEvents
}: WorkflowSSEOptions): WorkflowRunEvent[] {
  const queryClient = useQueryClient();
  const [liveEvents, setLiveEvents] = useState<WorkflowRunEvent[]>([]);
  const lastEventId = useRef(0);

  useEffect(() => {
    setLiveEvents([]);
    lastEventId.current = 0;
  }, [runId]);

  useEffect(() => {
    const max = Math.max(0, ...snapshotEvents.map((event) => event.sequence));
    lastEventId.current = Math.max(lastEventId.current, max);
  }, [snapshotEvents]);

  useEffect(() => {
    if (!runId || !active) return;
    const controller = new AbortController();
    let stopped = false;
    let frameId: number | null = null;
    let bufferedEvents: WorkflowRunEvent[] = [];

    const enqueueEvents = (values: WorkflowRunEvent[]) => {
      if (!values.length) return;
      bufferedEvents.push(...values);
      lastEventId.current = Math.max(
        lastEventId.current,
        ...values.map((event) => event.sequence)
      );
      if (frameId !== null) return;
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        const next = bufferedEvents;
        bufferedEvents = [];
        setLiveEvents((current) =>
          mergeWorkflowEvents(current, next, MAX_RETAINED_WORKFLOW_EVENTS)
        );
      });
    };

    const listen = async () => {
      let reconnectDelay = 500;
      while (!stopped && !controller.signal.aborted) {
        try {
          await api.streamWorkflowEvents(
            runId,
            (message) => {
              reconnectDelay = 500;
              if ("events" in message.data && "run" in message.data) {
                enqueueEvents(message.data.events);
              } else if ("sequence" in message.data) {
                const event = message.data;
                enqueueEvents([event]);
                if (event.event !== "node_output_delta") {
                  void queryClient.invalidateQueries({
                    queryKey: ["workflow-run", runId]
                  });
                }
              }
            },
            { signal: controller.signal, lastEventId: lastEventId.current }
          );
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ["workflow-run", runId] }),
            queryClient.invalidateQueries({ queryKey: ["workflow-runs", projectId] })
          ]);
          break;
        } catch {
          if (controller.signal.aborted || stopped) break;
          await delay(reconnectDelay);
          reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
        }
      }
    };

    void listen();
    return () => {
      stopped = true;
      controller.abort();
      if (frameId !== null) window.cancelAnimationFrame(frameId);
    };
  }, [active, projectId, queryClient, runId]);

  return useMemo(
    () =>
      mergeWorkflowEvents(
        snapshotEvents,
        liveEvents,
        MAX_RETAINED_WORKFLOW_EVENTS
      ),
    [liveEvents, snapshotEvents]
  );
}

export function mergeWorkflowEvents(
  first: WorkflowRunEvent[],
  second: WorkflowRunEvent[],
  limit = Number.POSITIVE_INFINITY
): WorkflowRunEvent[] {
  const values = new Map<number, WorkflowRunEvent>();
  for (const event of [...first, ...second]) values.set(event.sequence, event);
  const merged = [...values.values()].sort(
    (left, right) => left.sequence - right.sequence
  );
  return merged.length > limit ? merged.slice(-limit) : merged;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
