import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiTimeoutError,
  DEFAULT_REQUEST_TIMEOUT_MS,
  WorkflowStreamParseError,
  api,
  request,
  type ModelDebugRequest,
  type NormalizedStreamEvent
} from "./client";

const payload: ModelDebugRequest = {
  provider_account_id: 1,
  model: "mock-novel-v1",
  response_format: "text",
  messages: [{ role: "user", content: [{ type: "text", text: "测试" }] }]
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function pendingUntilAbort(_input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return new Promise((_resolve, reject) => {
    const rejectAbort = () => reject(init?.signal?.reason ?? new DOMException("Aborted", "AbortError"));
    if (init?.signal?.aborted) rejectAbort();
    else init?.signal?.addEventListener("abort", rejectAbort, { once: true });
  });
}

describe("non-stream request timeout", () => {
  it("aborts the underlying fetch and rejects with an explicit timeout error", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(pendingUntilAbort);
    vi.stubGlobal("fetch", fetchMock);

    const result = expect(request("/api/stuck")).rejects.toBeInstanceOf(ApiTimeoutError);
    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS);

    await result;
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });

  it("preserves a caller-provided abort signal", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(pendingUntilAbort));
    const controller = new AbortController();

    const result = expect(
      request("/api/cancelled", { signal: controller.signal })
    ).rejects.toMatchObject({ name: "AbortError" });
    controller.abort(new DOMException("Cancelled", "AbortError"));

    await result;
  });

  it("does not apply the short request timeout to streaming calls", async () => {
    vi.useFakeTimers();
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      }
    });
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(stream, { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    const streaming = api.streamModel(payload, () => undefined);
    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS + 1);

    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted ?? false).toBe(false);
    streamController?.close();
    await streaming;
  });
});

describe("model stream client", () => {
  it("reports malformed workflow SSE as a recognizable protocol error", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("id: 1\nevent: run_event\ndata: {broken-json}\n\n"));
        controller.close();
      }
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));

    await expect(
      api.streamWorkflowEvents(9, () => undefined)
    ).rejects.toBeInstanceOf(WorkflowStreamParseError);
  });

  it("parses UTF-8 and SSE records split across arbitrary response chunks", async () => {
    const source = [
      'event: start\r\ndata: {"sequence":1,"event":"start","text_delta":"","request_id":"req-1","tool_call":null,"usage":null,"error":null,"finish_reason":null,"warning":null}\r\n\r\n',
      'event: delta\ndata: {"sequence":2,"event":"delta","text_delta":"中文输出","request_id":"req-1","tool_call":null,"usage":null,"error":null,"finish_reason":null,"warning":null}\n\n'
    ].join("");
    const bytes = new TextEncoder().encode(source);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const [start, end] of [[0, 17], [17, 151], [151, 196], [196, 201], [201, bytes.length]]) {
          controller.enqueue(bytes.slice(start, end));
        }
        controller.close();
      }
    });
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } }));
    vi.stubGlobal("fetch", fetchMock);
    const events: NormalizedStreamEvent[] = [];

    await api.streamModel(payload, (event) => events.push(event));

    expect(events.map((event) => event.event)).toEqual(["start", "delta"]);
    expect(events[1].text_delta).toBe("中文输出");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({ provider_account_id: 1, stream: true });
  });

  it("builds a bounded workflow history cursor request", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response("[]", {
        status: 200,
        headers: { "content-type": "application/json" }
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.listWorkflowRuns(3, 7, 50, 900);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/api/workflow-runs?project_id=3&limit=50&workflow_id=7&before_id=900"
    );
  });
});
