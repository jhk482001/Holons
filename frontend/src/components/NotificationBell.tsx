import { useTranslation } from "react-i18next";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { NotificationsAPI, Notification } from "../api/client";
import "./NotificationBell.css";

const SEVERITY_COLOR: Record<string, string> = {
  info: "var(--ink-3)",
  warn: "var(--warn, #c98930)",
  error: "var(--danger)",
  critical: "var(--danger)",
};

/**
 * Resolve the target page for a notification based on which related_* id
 * or action_payload field is set. Used by the single arrow button on each
 * row. Falls back to /settings (where the full notification history lives).
 */
function notifTarget(n: Notification): string {
  const payload = (n.action_payload || {}) as Record<string, unknown>;
  if (n.related_run_id) return `/runs/${n.related_run_id}`;
  if (n.related_workflow_id) return `/workflows/${n.related_workflow_id}`;
  if (n.related_escalation_id) return `/escalations`;
  if (payload.thread_id) return `/dialog`;
  if (n.related_agent_id) return `/agents/${n.related_agent_id}`;
  return `/settings`;
}

const MAX_VISIBLE = 5;

export default function NotificationBell() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [dropdownPos, setDropdownPos] = useState<{ left: number; bottom: number; width: number } | null>(null);

  // Compute the dropdown's screen position from the bell button's bbox.
  // Re-runs when the dropdown opens or the window resizes.
  useLayoutEffect(() => {
    if (!open) {
      setDropdownPos(null);
      return;
    }
    function update() {
      const el = ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setDropdownPos({
        left: r.left,
        bottom: window.innerHeight - r.top + 8,
        width: Math.max(r.width, 360),
      });
    }
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  const { data: unread } = useQuery({
    queryKey: ["unread"],
    queryFn: NotificationsAPI.unreadCount,
    refetchInterval: 15_000,
  });

  const { data: notifications = [] } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => NotificationsAPI.list(),
    enabled: open,
  });

  const markAllRead = useMutation({
    mutationFn: () => NotificationsAPI.markAllRead(),
    onSuccess: () => {
      // Optimistically clear the badge so it disappears instantly.
      qc.setQueryData(["unread"], { count: 0 });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["unread"] });
    },
  });

  // Opening the dropdown with unread notifications → mark them all as read
  // in one shot. The badge clears and the rows stay visible so the user
  // still sees what just happened.
  useEffect(() => {
    if (open && (unread?.count ?? 0) > 0) {
      markAllRead.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Close on outside click — outside means neither the bell button nor
  // the portaled dropdown.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      const insideBell = ref.current?.contains(target);
      const insideDropdown = dropdownRef.current?.contains(target);
      if (!insideBell && !insideDropdown) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  const visible = notifications.slice(0, MAX_VISIBLE);
  const count = unread?.count ?? 0;

  function gotoNotification(n: Notification) {
    setOpen(false);
    navigate(notifTarget(n));
  }

  return (
    <div className="notif-bell-wrap" ref={ref}>
      <button
        className="notif-bell-btn"
        data-testid="notification-bell"
        onClick={() => setOpen((v) => !v)}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {count > 0 && (
          <span className="notif-bell-count" data-testid="notification-bell-count">
            {count}
          </span>
        )}
      </button>

      {open && dropdownPos && createPortal(
        <div
          ref={dropdownRef}
          className="notif-dropdown notif-dropdown-portal"
          data-testid="notification-dropdown"
          style={{
            position: "fixed",
            left: dropdownPos.left,
            bottom: dropdownPos.bottom,
            width: dropdownPos.width,
          }}
        >
          <div className="notif-head">
            <div>{t("notifications.title")}</div>
            <div className="notif-head-sub">
              {count > 0 ? t("notifications.unread", { count }) : t("notifications.showingRecent", { count: Math.min(visible.length, MAX_VISIBLE) })}
            </div>
          </div>
          <div className="notif-list">
            {visible.length === 0 ? (
              <div className="notif-empty">{t("notifications.empty")}</div>
            ) : (
              visible.map((n) => (
                <NotifRow
                  key={n.id}
                  n={n}
                  onGo={() => gotoNotification(n)}
                />
              ))
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

function NotifRow({
  n,
  onGo,
}: {
  n: Notification;
  onGo: () => void;
}) {
  const { t } = useTranslation();
  const color = SEVERITY_COLOR[n.severity] || "var(--ink-3)";
  return (
    <div
      className={`notif-row notif-${n.status}`}
      data-testid={`notification-row-${n.id}`}
    >
      <div className="notif-severity" style={{ background: color }} />
      <div className="notif-main">
        <div className="notif-title">{n.title}</div>
        {n.body && <div className="notif-body">{n.body}</div>}
      </div>
      <button
        type="button"
        className="notif-go"
        data-testid={`notif-go-${n.id}`}
        title={t("library.detail")}
        onClick={onGo}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 12h14" />
          <path d="M13 6l6 6-6 6" />
        </svg>
      </button>
    </div>
  );
}
