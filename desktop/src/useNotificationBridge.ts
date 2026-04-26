// Bridges the backend's `/api/notifications/unread_count` to:
//   1. macOS Dock badge label (always kept in sync with the count)
//   2. Native OS notification (Notification Center banner) — only when
//      the count *grows* AND the window isn't focused
//   3. Dock icon bounce — same trigger as #2
//
// We deliberately don't notify on the first poll (initial baseline) or
// when the window already has focus, to avoid noise. The bell is also
// kept in the title bar so the user has a visual fallback.
import { useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";

const POLL_INTERVAL_MS = 15_000;

async function ensureNotificationPermission(): Promise<boolean> {
  try {
    if (await isPermissionGranted()) return true;
    const r = await requestPermission();
    return r === "granted";
  } catch {
    return false;
  }
}

export function useNotificationBridge(enabled: boolean) {
  const lastCountRef = useRef<number | null>(null);
  const permissionAttemptedRef = useRef(false);

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const tick = async () => {
      try {
        const r = await fetch("/api/notifications/unread_count");
        if (!r.ok) return;
        const data = await r.json();
        const count = Number(data?.count) || 0;
        if (cancelled) return;

        // Always sync the dock badge to the current count, even on first
        // poll. macOS shows nothing for empty/0; show the number otherwise.
        invoke("set_dock_badge", { label: count > 0 ? String(count) : null })
          .catch(() => { /* macOS-only; no-op elsewhere */ });

        const prev = lastCountRef.current;
        lastCountRef.current = count;

        // First poll = baseline only; never fire on it.
        if (prev === null) return;
        if (count <= prev) return;

        // Don't pile on if the window is already in the user's face.
        const focused =
          typeof document !== "undefined" && document.hasFocus
            ? document.hasFocus()
            : false;
        if (focused) return;

        const delta = count - prev;
        if (await ensureNotificationPermission()) {
          sendNotification({
            title: "Holons",
            body: delta === 1
              ? "1 new notification"
              : `${delta} new notifications`,
          });
        }
        invoke("request_attention").catch(() => { /* no-op */ });
      } catch {
        // network blip / sidecar restart — silent
      }
    };

    // First, prompt for permission lazily — only when the user is
    // logged in. macOS's permission prompt is sticky, so this only
    // pops once. After grant, future ticks just send.
    if (!permissionAttemptedRef.current) {
      permissionAttemptedRef.current = true;
      ensureNotificationPermission();
    }

    tick(); // first call immediately, then poll
    timer = setInterval(tick, POLL_INTERVAL_MS);

    // Re-sync immediately whenever the window regains focus or becomes
    // visible — without this, after the user reads notifications via the
    // in-app bell the dock badge can lag up to 15s behind the real
    // unread count, which feels broken.
    const onFocus = () => { tick(); };
    const onVisibility = () => {
      if (typeof document !== "undefined" && !document.hidden) tick();
    };
    if (typeof window !== "undefined") {
      window.addEventListener("focus", onFocus);
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      if (typeof window !== "undefined") {
        window.removeEventListener("focus", onFocus);
        document.removeEventListener("visibilitychange", onVisibility);
      }
      // Clear badge on unmount (logout / window close).
      invoke("set_dock_badge", { label: null }).catch(() => {});
    };
  }, [enabled]);
}
