const TOKEN_FRAGMENT_KEY = "nas-token";
const TOKEN_SESSION_KEY = "nas.local-api-token";

export function initializeLocalApiToken(): void {
  if (typeof window === "undefined") return;
  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const token = fragment.get(TOKEN_FRAGMENT_KEY);
  if (!token) return;
  window.sessionStorage.setItem(TOKEN_SESSION_KEY, token);
  fragment.delete(TOKEN_FRAGMENT_KEY);
  const remaining = fragment.toString();
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}${remaining ? `#${remaining}` : ""}`
  );
}

initializeLocalApiToken();

export function withLocalApiToken(init?: RequestInit): RequestInit | undefined {
  if (typeof window === "undefined") return init;
  const token = window.sessionStorage.getItem(TOKEN_SESSION_KEY);
  if (!token) return init;
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return { ...init, headers };
}
