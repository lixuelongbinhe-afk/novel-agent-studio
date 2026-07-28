import { beforeEach, describe, expect, it } from "vitest";
import { initializeLocalApiToken, withLocalApiToken } from "./localAuth";

describe("local API token", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it("captures the fragment without leaving the token in browser history", () => {
    window.history.replaceState(null, "", "/project/1?tab=write#nas-token=secret-value&view=chat");

    initializeLocalApiToken();
    const request = withLocalApiToken({ headers: { Accept: "application/json" } });
    const headers = new Headers(request?.headers);

    expect(headers.get("Authorization")).toBe("Bearer secret-value");
    expect(headers.get("Accept")).toBe("application/json");
    expect(window.location.hash).toBe("#view=chat");
    expect(window.location.href).not.toContain("secret-value");
  });
});
