import { useEffect, useRef, useState } from "react";

import { api, type ModelDebugRequest } from "../../../api/client";

export type ModelStreamMeta = {
  tokens: number;
  requestId: string;
  finishReason: string;
  tokenSource: string;
};

export function useModelDebugStream() {
  const [output, setOutput] = useState("");
  const [streamError, setStreamError] = useState("");
  const [meta, setMeta] = useState<ModelStreamMeta | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [control, setControl] = useState<Record<string, unknown> | null>(null);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function run(payload: ModelDebugRequest) {
    const controller = new AbortController();
    abortRef.current = controller;
    setOutput("");
    setMeta(null);
    setStreamError("");
    setWarnings([]);
    setControl(null);
    setStreaming(true);
    let requestId = "";
    let tokens = 0;
    try {
      await api.streamModel(
        payload,
        (event) => {
          requestId = event.request_id ?? requestId;
          if (event.event === "delta") {
            setOutput((value) => value + event.text_delta);
          }
          if (event.event === "tool_call_delta" && event.tool_call) {
            setOutput((value) =>
              `${value}\n[tool] ${event.tool_call?.name}: ${JSON.stringify(event.tool_call?.arguments)}`.trim()
            );
          }
          if (event.event === "usage" && event.usage) {
            tokens = event.usage.total_tokens;
            setMeta({
              tokens,
              requestId,
              finishReason: "streaming",
              tokenSource:
                event.usage.source ??
                (event.usage.estimated ? "local_approximation" : "provider_actual")
            });
          }
          if (event.event === "warning" && event.warning) {
            setWarnings((current) => [...current, event.warning as string]);
          }
          if (event.event === "error" && event.error) {
            setStreamError(`${event.error.code}: ${event.error.message}`);
          }
          if (event.event === "done") {
            setMeta((current) => ({
              tokens,
              requestId,
              finishReason: event.finish_reason ?? "stop",
              tokenSource: current?.tokenSource ?? "local_approximation"
            }));
          }
        },
        controller.signal
      );
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setStreamError(error instanceof Error ? error.message : "流式请求失败");
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setStreaming(false);
    }
  }

  function stop() {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }

  return {
    output,
    setOutput,
    streamError,
    setStreamError,
    meta,
    setMeta,
    warnings,
    setWarnings,
    control,
    setControl,
    streaming,
    run,
    stop
  };
}
