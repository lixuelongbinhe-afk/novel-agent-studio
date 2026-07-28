import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api, type WorkflowRunEvent } from "../../../api/client";

type WorkflowSSEOptions = {
  runId: number | null;
  projectId: number;
  active: boolean;
  snapshotEvents: WorkflowRunEvent[];
};

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
        setLiveEvents((current) => mergeWorkflowEvents(current, next));
      });
    };

    const listen = async () => {
      while (!stopped && !controller.signal.aborted) {
        try {
          await api.streamWorkflowEvents(
            runId,
            (message) => {
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
          await delay(500);
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
    () => mergeWorkflowEvents(snapshotEvents, liveEvents),
    [liveEvents, snapshotEvents]
  );
}

export function mergeWorkflowEvents(
  first: WorkflowRunEvent[],
  second: WorkflowRunEvent[]
): WorkflowRunEvent[] {
  const values = new Map<number, WorkflowRunEvent>();
  for (const event of [...first, ...second]) values.set(event.sequence, event);
  return [...values.values()].sort((left, right) => left.sequence - right.sequence);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
