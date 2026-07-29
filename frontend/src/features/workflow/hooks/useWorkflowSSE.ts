import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  WorkflowStreamParseError,
  api,
  type WorkflowRunEvent
} from "../../../api/client";

type WorkflowSSEOptions = {
  runId: number | null;
  projectId: number;
  active: boolean;
  snapshotEvents: WorkflowRunEvent[];
};

export const MAX_RETAINED_WORKFLOW_EVENTS = 500;
const MAX_RECONNECT_DELAY_MS = 8_000;
export const MAX_WORKFLOW_RECONNECT_ATTEMPTS = 6;

export type WorkflowSSEState = {
  events: WorkflowRunEvent[];
  error: string | null;
};

export function useWorkflowSSE({
  runId,
  projectId,
  active,
  snapshotEvents
}: WorkflowSSEOptions): WorkflowSSEState {
  const queryClient = useQueryClient();
  const [liveEvents, setLiveEvents] = useState<WorkflowRunEvent[]>([]);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const lastEventId = useRef(0);

  useEffect(() => {
    setLiveEvents([]);
    setConnectionError(null);
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
      let reconnectAttempts = 0;
      while (!stopped && !controller.signal.aborted) {
        try {
          await api.streamWorkflowEvents(
            runId,
            (message) => {
              reconnectAttempts = 0;
              setConnectionError(null);
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
        } catch (error) {
          if (controller.signal.aborted || stopped) break;
          if (
            isPermanentWorkflowStreamError(error) ||
            reconnectAttempts >= MAX_WORKFLOW_RECONNECT_ATTEMPTS
          ) {
            setConnectionError(workflowStreamErrorMessage(error, false));
            break;
          }
          const reconnectDelay = workflowReconnectDelay(reconnectAttempts);
          reconnectAttempts += 1;
          setConnectionError(workflowStreamErrorMessage(error, true, reconnectAttempts));
          try {
            await delay(reconnectDelay, controller.signal);
          } catch {
            break;
          }
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

  const events = useMemo(
    () => mergeWorkflowEvents(
        snapshotEvents,
        liveEvents,
        MAX_RETAINED_WORKFLOW_EVENTS
      ),
    [liveEvents, snapshotEvents]
  );
  return useMemo(() => ({ events, error: connectionError }), [connectionError, events]);
}

export function isPermanentWorkflowStreamError(error: unknown): boolean {
  return (
    error instanceof WorkflowStreamParseError ||
    (error instanceof ApiError && [401, 403, 404].includes(error.status))
  );
}

export function workflowReconnectDelay(attempt: number): number {
  return Math.min(500 * (2 ** attempt), MAX_RECONNECT_DELAY_MS);
}

function workflowStreamErrorMessage(
  error: unknown,
  retrying: boolean,
  attempt = 0
): string {
  const detail = error instanceof Error && error.message ? error.message : "未知错误";
  return retrying
    ? `工作流实时连接失败，${workflowReconnectDelay(attempt - 1) / 1_000} 秒后进行第 ${attempt} 次重连：${detail}`
    : `工作流实时连接已停止：${detail}`;
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

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    const abort = () => {
      window.clearTimeout(timer);
      reject(signal.reason);
    };
    signal.addEventListener("abort", abort, { once: true });
  });
}
