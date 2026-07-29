const TOKEN_FRAGMENT_KEY = "nas-token";
const TOKEN_SESSION_KEY = "nas.local-api-token";
const DESKTOP_FRAGMENT_KEY = "nas-desktop";
const BRIDGE_READY_TIMEOUT_MS = 10_000;

declare global {
  interface Window {
    pywebview?: {
      api?: {
        consume_local_api_token?: () => Promise<string>;
      };
    };
  }
}

export async function initializeLocalApiToken(): Promise<void> {
  if (typeof window === "undefined") return;
  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const token = fragment.get(TOKEN_FRAGMENT_KEY);
  const desktopMode = fragment.get(DESKTOP_FRAGMENT_KEY) === "1";
  if (token) window.sessionStorage.setItem(TOKEN_SESSION_KEY, token);
  fragment.delete(TOKEN_FRAGMENT_KEY);
  fragment.delete(DESKTOP_FRAGMENT_KEY);
  const remaining = fragment.toString();
  if (token || desktopMode) {
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}${remaining ? `#${remaining}` : ""}`
    );
  }
  if (!token && desktopMode) {
    const bridgeToken = await readDesktopBridgeToken();
    if (bridgeToken) window.sessionStorage.setItem(TOKEN_SESSION_KEY, bridgeToken);
  }
}

export const localApiTokenReady = initializeLocalApiToken();

export function withLocalApiToken(init?: RequestInit): RequestInit | undefined {
  if (typeof window === "undefined") return init;
  const token = window.sessionStorage.getItem(TOKEN_SESSION_KEY);
  if (!token) return init;
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return { ...init, headers };
}

async function readDesktopBridgeToken(): Promise<string> {
  const consume = window.pywebview?.api?.consume_local_api_token;
  if (consume) return consume();
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: string) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      window.removeEventListener("pywebviewready", onReady);
      resolve(value);
    };
    const onReady = () => {
      const bridge = window.pywebview?.api?.consume_local_api_token;
      if (!bridge) finish("");
      else void bridge().then(finish, () => finish(""));
    };
    const timeout = window.setTimeout(() => finish(""), BRIDGE_READY_TIMEOUT_MS);
    window.addEventListener("pywebviewready", onReady, { once: true });
  });
}
