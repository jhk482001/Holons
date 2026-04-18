import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FeatureFlagsAPI } from "../../api/client";

export default function SystemSettingsTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: flags = [], isLoading } = useQuery({
    queryKey: ["feature-flags"],
    queryFn: FeatureFlagsAPI.list,
  });

  const toggle = useMutation({
    mutationFn: ({ feature, admin_only }: { feature: string; admin_only: boolean }) =>
      FeatureFlagsAPI.setAdminOnly(feature, admin_only),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["feature-flags"] }),
  });

  return (
    <div data-testid="settings-system-tab">
      <section style={{ marginTop: 8 }}>
        <h3 style={{ fontSize: 15, fontWeight: 800, marginBottom: 6 }}>{t("system.permissions")}</h3>
        <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 16 }}>
          {t("system.permissionsDesc")}
        </div>

        {isLoading ? (
          <div style={{ padding: 30, color: "var(--ink-4)", textAlign: "center" }}>{t("system.loading")}</div>
        ) : (
          <div
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 14,
              overflow: "hidden",
            }}
          >
            {flags.map((f, i) => (
              <div
                key={f.feature}
                data-testid={`feature-flag-${f.feature}`}
                style={{
                  padding: "16px 20px",
                  borderTop: i === 0 ? "none" : "1px solid var(--border)",
                  display: "flex",
                  alignItems: "center",
                  gap: 16,
                }}
              >
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 700 }}>{f.label}</div>
                  {f.description && (
                    <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 3 }}>
                      {f.description}
                    </div>
                  )}
                  <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 4, fontFamily: "monospace" }}>
                    {f.feature}
                  </div>
                </div>
                <label
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    cursor: "pointer",
                    userSelect: "none",
                  }}
                >
                  <input
                    type="checkbox"
                    data-testid={`feature-flag-${f.feature}-checkbox`}
                    checked={f.admin_only}
                    disabled={toggle.isPending}
                    onChange={(e) =>
                      toggle.mutate({ feature: f.feature, admin_only: e.target.checked })
                    }
                    style={{ width: 18, height: 18, cursor: "pointer" }}
                  />
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      color: f.admin_only ? "var(--accent)" : "var(--ink-3)",
                      minWidth: 70,
                    }}
                  >
                    {f.admin_only ? t("system.adminOnly") : t("system.allUsers")}
                  </span>
                </label>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
