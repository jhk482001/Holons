/**
 * API adapter for the Tauri desktop app.
 *
 * Supports three modes:
 *   1. Enterprise — connects to a remote server URL (user-configured)
 *   2. Personal  — connects to a local sidecar backend (auto port)
 *   3. Dev       — uses Vite proxy on localhost:1420 (dev mode only)
 *
 * The Vite dev proxy handles /api in dev mode, so BACKEND_BASE is only
 * used in production builds or when the user explicitly sets a server URL.
 */
import { load } from "@tauri-apps/plugin-store";

export type AppMode = "enterprise" | "personal" | null;
export type DesktopLang = "en" | "zh-TW";

interface ConnectionConfig {
  mode: AppMode;
  serverUrl: string; // e.g. "https://agent.company.com" or "http://localhost:9123"
  token: string | null;
  lang: DesktopLang;
}

let _config: ConnectionConfig = {
  mode: null,
  serverUrl: "",
  token: null,
  lang: "en",
};

export async function initApiAdapter() {
  try {
    const store = await load("session.json");
    _config.mode = (await store.get<AppMode>("app_mode")) ?? null;
    _config.serverUrl = (await store.get<string>("server_url")) ?? "";
    _config.token = (await store.get<string>("desktop_token")) ?? null;
    _config.lang = (await store.get<DesktopLang>("lang")) ?? "en";
  } catch {
    // First run or corrupted store
  }
  // In personal mode the sidecar picks a new port on every launch, so the
  // stored server_url is guaranteed stale. Clear it before any fetch fires
  // — DesktopApp's respawn effect will write the fresh port back.
  if (_config.mode === "personal") {
    _config.serverUrl = "";
  }
  (globalThis as any).window.__HOLONS_API_BASE__ = _config.serverUrl;
  patchFetch();
}

export function getConfig(): ConnectionConfig {
  return { ..._config };
}

export function getToken(): string | null {
  return _config.token;
}

export async function setConnectionConfig(mode: AppMode, serverUrl: string) {
  _config.mode = mode;
  _config.serverUrl = serverUrl.replace(/\/+$/, ""); // strip trailing slash
  const store = await load("session.json");
  await store.set("app_mode", mode);
  await store.set("server_url", _config.serverUrl);
  // Expose to shared avatar helpers so <img src> resolves against sidecar.
  (globalThis as any).window.__HOLONS_API_BASE__ = _config.serverUrl;
}

export function setToken(token: string | null) {
  _config.token = token;
  load("session.json").then((store) => {
    if (token) {
      store.set("desktop_token", token);
    } else {
      store.delete("desktop_token");
    }
  });
}

export async function clearAllConfig() {
  _config = { mode: null, serverUrl: "", token: null, lang: _config.lang };
  const store = await load("session.json");
  await store.clear();
}

export function getLang(): DesktopLang {
  return _config.lang;
}

export async function saveLang(lang: DesktopLang) {
  _config.lang = lang;
  const store = await load("session.json");
  await store.set("lang", lang);
}

/**
 * Resolve the base URL for API calls.
 * - In dev mode (Vite proxy active on :1420): return "" (relative /api works)
 * - Enterprise: return the configured server URL
 * - Personal: return "http://localhost:{sidecar_port}"
 */
function resolveBase(): string {
  // Dev mode: Vite proxy handles /api → backend
  // @ts-ignore — Vite injects import.meta.env at build time
  if (typeof import.meta !== "undefined" && (import.meta as any).env?.DEV) return "";
  return _config.serverUrl || "";
}

/** Convert a relative `/api/...` path into an absolute URL against the
 *  current connection. For `<img src>` / `<a href>` usage where Tauri
 *  can't rely on our fetch() patch. */
export function absoluteUrl(path: string): string {
  if (!path.startsWith("/")) return path;
  const base = resolveBase();
  return base ? base + path : path;
}

function patchFetch() {
  const original = globalThis.fetch;
  globalThis.fetch = function patchedFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    let url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;

    // Rewrite relative /api paths to absolute backend URL
    const base = resolveBase();
    if (base && url.startsWith("/api")) {
      url = base + url;
    }

    // Add auth header
    const headers = new Headers(init?.headers);
    if (_config.token && !headers.has("authorization")) {
      headers.set("x-desktop-token", _config.token);
    }

    return original.call(globalThis, url, { ...init, headers });
  };
}
