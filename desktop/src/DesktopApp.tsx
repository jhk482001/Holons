import { useEffect, useState, useCallback, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Store } from "@tauri-apps/plugin-store";
import { getConfig, getToken, setToken, setConnectionConfig, saveLang, DesktopLang, AppMode } from "./api-adapter";
import ConnectionSetup from "./ConnectionSetup";
import DesktopLogin from "./DesktopLogin";
import DesktopDialog from "./DesktopDialog";
import NormalModeApp from "./NormalModeApp";
import "./desktop.css";

type WindowMode = "normal" | "overlay";
const MODE_STORE_KEY = "window_mode";

export default function DesktopApp() {
  const { t, i18n } = useTranslation();
  const [appMode, setAppMode] = useState<AppMode>(getConfig().mode);
  const [token, setLocalToken] = useState<string | null>(getToken());
  const [sidecarReady, setSidecarReady] = useState(appMode !== "personal");
  const webOpenedRef = useRef(false);
  // Window display mode — "normal" is the new CleanMyMac-style decorated
  // floating window with the full web UI, "overlay" is the existing
  // borderless transparent always-on-top dialog. Persisted via the
  // tauri-plugin-store so it survives across launches.
  const [windowMode, setWindowMode] = useState<WindowMode>("normal");

  // In personal mode the sidecar picks a fresh port on every launch. The
  // first-run ConnectionSetup saved an obsolete port; on every subsequent
  // launch we must respawn the sidecar and overwrite server_url with the
  // current port, otherwise every fetch hits a dead socket and login fails.
  useEffect(() => {
    let cancelled = false;
    if (appMode !== "personal") return;
    (async () => {
      try {
        const port = await invoke<number>("start_sidecar");
        const url = `http://localhost:${port}`;
        // Wait for health before declaring ready — sidecar just forked.
        for (let i = 0; i < 30 && !cancelled; i++) {
          try {
            const r = await fetch(`${url}/api/health`, {
              signal: AbortSignal.timeout(2_000),
            });
            if (r.ok) break;
          } catch { /* not ready yet */ }
          await new Promise((r) => setTimeout(r, 1_000));
        }
        if (cancelled) return;
        await setConnectionConfig("personal", url);
        setSidecarReady(true);
      } catch (e) {
        console.error("sidecar respawn failed:", e);
        setSidecarReady(true); // unblock UI so user sees ConnectionSetup reset
      }
    })();
    return () => { cancelled = true; };
  }, [appMode]);

  const needsSetup = !appMode;
  const { data: me, isError } = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const resp = await fetch("/api/me");
      if (!resp.ok) throw new Error("not authed");
      return resp.json();
    },
    // Must wait for sidecarReady — otherwise this fires against a stale
    // port from the previous launch and returns CORS/connection errors.
    enabled: !!token && !needsSetup && sidecarReady,
    retry: false,
  });
  const isLoggedIn = !needsSetup && !!token && !!me && !isError;

  const handleLogin = useCallback((newToken: string) => {
    setToken(newToken);
    setLocalToken(newToken);
  }, []);

  const handleLogout = useCallback(() => {
    setToken(null);
    setLocalToken(null);
  }, []);

  // Tray events
  useEffect(() => {
    const u1 = listen("reset-connection", async () => {
      const { clearAllConfig } = await import("./api-adapter");
      await clearAllConfig();
      window.location.reload();
    });
    const u2 = listen<string>("set-lang", async (e) => {
      const lang = e.payload as DesktopLang;
      if (lang === "en" || lang === "zh-TW") {
        await i18n.changeLanguage(lang);
        await saveLang(lang);
      }
    });
    const u3 = listen<string>("set-mode", async (e) => {
      const next = (e.payload as WindowMode) || "normal";
      await applyWindowMode(next);
    });
    return () => {
      u1.then((fn) => fn());
      u2.then((fn) => fn());
      u3.then((fn) => fn());
    };
  }, [i18n]);

  // Load + apply persisted window mode on first render. New installs land
  // on "normal" (the CleanMyMac-style floating web UI). Existing users
  // who picked "overlay" keep their preference across launches.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const store = await Store.load("holons-desktop.json");
        const saved = (await store.get<WindowMode>(MODE_STORE_KEY)) || "normal";
        if (cancelled) return;
        await applyWindowMode(saved);
      } catch {
        // Plugin not available in dev or store corrupt → default to normal
        if (!cancelled) await applyWindowMode("normal");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function applyWindowMode(mode: WindowMode) {
    setWindowMode(mode);
    // Tell the document so desktop.css can pick the right body bg —
    // overlay = fully transparent (no halo), normal = warm-cream
    // translucent material.
    if (typeof document !== "undefined") {
      document.body.dataset.mode = mode;
    }
    try {
      await invoke("set_window_mode", { mode });
    } catch (e) {
      console.warn("[set_window_mode]", e);
    }
    try {
      const store = await Store.load("holons-desktop.json");
      await store.set(MODE_STORE_KEY, mode);
      await store.save();
    } catch {
      // ignore — preference will reset to default next launch
    }
  }

  // Open web UI in default browser once, right after first successful login.
  useEffect(() => {
    if (!isLoggedIn || webOpenedRef.current) return;
    const url = getConfig().serverUrl;
    if (!url) return;
    webOpenedRef.current = true;
    invoke("open_url", { url }).catch((err) => {
      console.error("[open_url] failed:", err);
    });
  }, [isLoggedIn]);

  // Click-through is ONLY meaningful in overlay mode (transparent
  // borderless always-on-top window where empty regions should pass
  // clicks through to the desktop). In normal mode the window is a
  // standard decorated window and we never want clicks to leak.
  useClickThrough(windowMode !== "overlay");

  if (needsSetup) {
    return (
      <ConnectionSetup
        onComplete={(mode) => {
          setAppMode(mode);
          window.location.reload();
        }}
      />
    );
  }

  if (appMode === "personal" && !sidecarReady) {
    return (
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "center",
        height: "100vh", color: "#888", fontSize: 13,
      }}>
        {t("common.startingBackend")}
      </div>
    );
  }

  if (!isLoggedIn) {
    return <DesktopLogin onLogin={handleLogin} />;
  }

  if (windowMode === "normal") {
    return <NormalModeApp />;
  }
  return <DesktopDialog me={me} onLogout={handleLogout} />;
}


/**
 * Click-through: track mouse over [data-interactive] elements.
 * - Over transparent area → ignore=true (clicks pass to desktop)
 * - Over interactive element → ignore=false (clicks reach our UI)
 *
 * This works on macOS because transparent Tauri windows still receive
 * mousemove events even when setIgnoreCursorEvents(true) is set,
 * thanks to macOSPrivateApi + transparent window mode.
 */
function useClickThrough(disabled = false) {
  const ignoring = useRef(false);
  const moveCount = useRef(0);

  useEffect(() => {
    if (disabled) {
      // Normal mode → never ignore cursor events; reset to default
      // before bailing so the very first switch back to overlay starts
      // from a known state.
      invoke("set_click_through", { ignore: false }).catch(() => {});
      ignoring.current = false;
      return;
    }
    function onMove(e: MouseEvent) {
      moveCount.current++;
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const isOverInteractive = el?.closest("[data-interactive]") !== null;

      // Debug: log every 30th event to console
      if (moveCount.current % 30 === 0) {
        console.log(`[click-through] move#${moveCount.current} interactive=${isOverInteractive} ignoring=${ignoring.current} el=${el?.tagName}.${el?.className?.toString().slice(0,30)}`);
      }

      if (isOverInteractive && ignoring.current) {
        console.log("[click-through] → interactive zone, setting ignore=false + focus");
        invoke("set_click_through", { ignore: false });
        invoke("focus_window");
        ignoring.current = false;
      } else if (!isOverInteractive && !ignoring.current) {
        console.log("[click-through] → transparent zone, setting ignore=true");
        invoke("set_click_through", { ignore: true });
        ignoring.current = true;
      }
    }

    console.log("[click-through] hook mounted, setting ignore=false");
    invoke("set_click_through", { ignore: false });
    ignoring.current = false;

    document.addEventListener("mousemove", onMove);
    return () => document.removeEventListener("mousemove", onMove);
  }, [disabled]);
}
