import { useTranslation } from "react-i18next";
import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AssetsAPI, AssetRow, AssetKind } from "../api/client";

/**
 * Agent detail → "Assets" tab. Shows every asset the current user can see
 * (owned + granted), grouped by kind, with a checkbox that assigns /
 * unassigns it to/from this agent. Lets the operator wire up MCP servers,
 * RAG sources, and built-in tools without leaving the agent page.
 */
export default function AgentAssetsEditor({ agentId }: { agentId: number }) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  // Every asset the caller can see (owned + granted + admin-wide)
  const { data: allAssets = [], isLoading: loadingAll } = useQuery({
    queryKey: ["assets-all"],
    queryFn: () => AssetsAPI.list(),
  });
  // Assets currently assigned to this agent
  const { data: assigned = [], isLoading: loadingBound } = useQuery({
    queryKey: ["agent-assets", agentId],
    queryFn: () => AssetsAPI.listAssetsForAgent(agentId),
  });

  const assignedIds = useMemo(() => new Set(assigned.map((a) => a.id)), [assigned]);

  const assignMut = useMutation({
    mutationFn: (asset_id: number) => AssetsAPI.assignToAgent(agentId, asset_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-assets", agentId] }),
  });
  const unassignMut = useMutation({
    mutationFn: (asset_id: number) => AssetsAPI.unassignFromAgent(agentId, asset_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-assets", agentId] }),
  });

  if (loadingAll || loadingBound) {
    return <div style={{ padding: 30, textAlign: "center", color: "var(--ink-4)" }}>{t("assets.loading")}</div>;
  }

  const byKind = groupBy(allAssets);
  const kinds: { key: AssetKind; label: string }[] = [
    { key: "skill", label: t("library.tab.skill") },
    { key: "tool", label: t("library.tab.tool") },
    { key: "mcp", label: t("library.tab.mcp") },
    { key: "rag", label: t("library.tab.rag") },
  ];

  return (
    <div data-testid="agent-assets-editor">
      <div
        style={{
          fontSize: 12,
          color: "var(--ink-3)",
          marginBottom: 14,
          padding: "10px 14px",
          background: "var(--accent-soft)",
          borderRadius: 10,
        }}
      >
        {t("assets.help")}
      </div>
      {kinds.map(({ key, label }) => {
        const rows = byKind[key] || [];
        return (
          <section key={key} style={{ marginBottom: 22 }}>
            <h4 style={{ fontSize: 12, fontWeight: 800, margin: "0 0 8px 0", textTransform: "uppercase" }}>
              {label} <span style={{ fontWeight: 500, color: "var(--ink-4)" }}>· {rows.length}</span>
            </h4>
            {rows.length === 0 ? (
              <div
                style={{
                  padding: 14,
                  background: "var(--surface-2)",
                  borderRadius: 8,
                  fontSize: 11,
                  color: "var(--ink-4)",
                }}
              >
                {t("assets.empty", { kind: label })}
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {rows.map((a) => {
                  const bound = assignedIds.has(a.id);
                  return (
                    <label
                      key={a.id}
                      className={`asset-pick-row${bound ? " checked" : ""}`}
                      data-testid={`agent-asset-row-${a.id}`}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        padding: "8px 12px",
                        background: bound ? "var(--accent-soft)" : "var(--surface)",
                        border: `1px solid ${bound ? "var(--accent)" : "var(--border)"}`,
                        borderRadius: 10,
                        cursor: "pointer",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={bound}
                        data-testid={`agent-asset-checkbox-${a.id}`}
                        onChange={(e) => {
                          if (e.target.checked) assignMut.mutate(a.id);
                          else unassignMut.mutate(a.id);
                        }}
                        style={{ width: 16, height: 16, cursor: "pointer" }}
                      />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>
                          {a.name}
                          {!a.enabled && (
                            <span style={{ marginLeft: 8, fontSize: 9, color: "var(--ink-4)" }}>
                              {t("assets.disabled")}
                            </span>
                          )}
                        </div>
                        {a.description && (
                          <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>
                            {a.description}
                          </div>
                        )}
                      </div>
                      <div style={{ fontSize: 10, color: "var(--ink-4)" }}>
                        {a.owner_display_name || a.owner_username || `#${a.owner_user_id}`}
                      </div>
                    </label>
                  );
                })}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function groupBy(rows: AssetRow[]): Record<AssetKind, AssetRow[]> {
  const out: Record<AssetKind, AssetRow[]> = { skill: [], tool: [], mcp: [], rag: [] };
  for (const r of rows) out[r.kind].push(r);
  return out;
}
