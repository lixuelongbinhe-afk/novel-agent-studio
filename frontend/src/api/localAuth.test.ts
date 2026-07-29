import { beforeEach, describe, expect, it, vi } from "vitest";
import { initializeLocalApiToken, withLocalApiToken } from "./localAuth";

describe("local API token", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it("captures the fragment without leaving the token in browser history", async () => {
    window.history.replaceState(null, "", "/project/1?tab=write#nas-token=secret-value&view=chat");

    await initializeLocalApiToken();
    const request = withLocalApiToken({ headers: { Accept: "application/json" } });
    const headers = new Headers(request?.headers);

    expect(headers.get("Authorization")).toBe("Bearer secret-value");
    expect(headers.get("Accept")).toBe("application/json");
    expect(window.location.hash).toBe("#view=chat");
    expect(window.location.href).not.toContain("secret-value");
  });

  it("receives a desktop token through the bridge without putting it in the URL", async () => {
    const consume = vi.fn(async () => "bridge-secret");
    window.pywebview = { api: { consume_local_api_token: consume } };
    window.history.replaceState(null, "", "/#nas-desktop=1");

    await initializeLocalApiToken();
    const headers = new Headers(withLocalApiToken()?.headers);

    expect(consume).toHaveBeenCalledTimes(1);
    expect(headers.get("Authorization")).toBe("Bearer bridge-secret");
    expect(window.location.href).not.toContain("bridge-secret");
    expect(window.location.hash).toBe("");
    delete window.pywebview;
  });
});
