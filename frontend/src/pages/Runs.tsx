import { useTranslation } from "react-i18next";
import { useMemo } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { RunsAPI, RunListItem } from "../api/client";

function useStatusLabel(): Record<string, string> {
  const { t } = useTranslation();
  return {
    running: t("runs.statusLabels.running"),
    done: t("runs.statusLabels.done"),
    error: t("runs.statusLabels.error"),
    cancelling: t("runs.statusLabels.cancelling"),
    cancelled: t("runs.statusLabels.cancelled"),
    queued: t("runs.statusLabels.queued"),
    paused: t("runs.statusLabels.paused"),
  };
}

const PAGE_SIZE = 50;

export default function Runs() {
  const { t } = useTranslation();
  const STATUS_LABEL = useStatusLabel();
  const qc = useQueryClient();
  const navigate = useNavigate();

  // Cursor-paginated list. The first page is the newest PAGE_SIZE runs,
  // older pages are fetched on demand via the "載入更多" button at the
  // bottom. The regression bug this page used to have: old hard-coded
  // LIMIT 50 hid everything past the 50th newest run with no way to see
  // them — now they're just one click away.
  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetching,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["runs"],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      RunsAPI.list({ limit: PAGE_SIZE, before_id: pageParam }),
    getNextPageParam: (lastPage) => {
      if (!lastPage.has_more || lastPage.runs.length === 0) return undefined;
      return lastPage.runs[lastPage.runs.length - 1].id;
    },
    refetchInterval: 5_000,
  });

  const runs = useMemo<RunListItem[]>(
    () => (data?.pages.flatMap((p) => p?.runs ?? []) ?? []).filter(Boolean),
    [data],
  );

  const stop = useMutation({
    mutationFn: (id: number) => RunsAPI.stop(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });

  return (
    <div className="page">
      <h1>{t("runs.title")}</h1>
      <div className="subtitle">{t("runs.subtitle", { count: runs.length })}</div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {runs.map((r) => (
          <div
            key={r.id}
            data-testid={`run-row-${r.id}`}
            onClick={() => navigate(`/runs/${r.id}`)}
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 14,
              padding: "16px 20px",
              display: "flex",
              alignItems: "center",
              gap: 16,
              cursor: "pointer",
              transition: "transform 0.15s, box-shadow 0.15s",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.boxShadow = "var(--shadow-sm)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.boxShadow = "";
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>
                Run #{r.id}
                {r.workflow_name && (
                  <span
                    style={{
                      marginLeft: 8,
                      fontSize: 11,
                      color: "var(--ink-3)",
                      fontWeight: 500,
                    }}
                  >
                    · {r.workflow_name}
                  </span>
                )}
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: "var(--ink-3)",
                  marginTop: 2,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {r.initial_input?.slice(0, 120)}
              </div>
            </div>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                padding: "4px 10px",
                borderRadius: 999,
                background:
                  r.status === "done"
                    ? "var(--good-soft)"
                    : r.status === "error"
                      ? "var(--danger-soft)"
                      : "var(--surface-2)",
                color:
                  r.status === "done"
                    ? "var(--good)"
                    : r.status === "error"
                      ? "var(--danger)"
                      : "var(--ink-2)",
              }}
            >
              {STATUS_LABEL[r.status] || r.status}
            </div>
            <div style={{ fontSize: 11, color: "var(--ink-3)", minWidth: 90, textAlign: "right" }}>
              <div>${(Number(r.total_cost_usd) || 0).toFixed(4)}</div>
              <div>
                {((Number(r.total_input_tokens) || 0) + (Number(r.total_output_tokens) || 0)).toLocaleString()} tokens
              </div>
            </div>
            {(r.status === "running" || r.status === "queued") && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  stop.mutate(r.id);
                }}
                style={{
                  padding: "6px 14px",
                  border: "1px solid var(--danger)",
                  color: "var(--danger)",
                  background: "white",
                  borderRadius: 8,
                  fontSize: 11,
                  fontWeight: 700,
                }}
              >
                {t("runs.stop")}
              </button>
            )}
          </div>
        ))}
        {runs.length === 0 && !isFetching && (
          <div style={{ textAlign: "center", color: "var(--ink-4)", padding: 60 }}>
            {t("runs.empty")}
          </div>
        )}
        {hasNextPage && (
          <button
            onClick={() => fetchNextPage()}
            disabled={isFetchingNextPage}
            data-testid="runs-load-more"
            style={{
              padding: "12px 20px",
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              fontSize: 12,
              fontWeight: 700,
              color: "var(--ink-3)",
              cursor: "pointer",
              marginTop: 4,
            }}
          >
            {isFetchingNextPage ? t("btn.loading") : t("runs.loadMore")}
          </button>
        )}
      </div>
    </div>
  );
}
