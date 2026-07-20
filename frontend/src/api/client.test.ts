import { afterEach, describe, expect, it, vi } from "vitest";
import { api, type ModelDebugRequest, type NormalizedStreamEvent } from "./client";

const payload: ModelDebugRequest = {
  provider_account_id: 1,
  model: "mock-novel-v1",
  response_format: "text",
  messages: [{ role: "user", content: [{ type: "text", text: "测试" }] }]
};

afterEach(() => vi.unstubAllGlobals());

describe("model stream client", () => {
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
