import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ModelClientsAPI } from "../api/client";

/**
 * Persistent yellow banner mounted in the main layout. Shows whenever
 * the user has zero usable model clients — i.e. every client is either
 * disabled, missing credentials, or has a `last_test_status = 'fail'`.
 *
 * Without a usable client, no agent can run. This prevents the
 * "click 'send' and nothing happens" confusion where users assume
 * things work until they try.
 */
export default function NoModelClientBanner() {
  const { t } = useTranslation();
  const { data: clients = [], isLoading } = useQuery({
    queryKey: ["model-client-usable-count"],
    queryFn: ModelClientsAPI.list,
    refetchInterval: 30_000,
  });

  if (isLoading) return null;
  const usable = clients.filter((c) =>
    c.enabled && c.has_credential && c.last_test_status !== "fail",
  );
  if (usable.length > 0) return null;

  const someFail = clients.some((c) => c.last_test_status === "fail");
  return (
    <div style={{
      background: "#fef3c7",
      borderBottom: "1px solid #fbbf24",
      color: "#78350f",
      padding: "10px 20px",
      fontSize: 13,
      display: "flex",
      alignItems: "center",
      gap: 14,
    }}>
      <span style={{ fontSize: 16 }}>⚠️</span>
      <div style={{ flex: 1 }}>
        <strong>{t("noModel.title")}</strong>
        <div style={{ fontSize: 12, marginTop: 2, color: "#92400e" }}>
          {clients.length === 0
            ? t("noModel.noneExist")
            : someFail
              ? t("noModel.allFailing")
              : t("noModel.noneUsable")}
        </div>
      </div>
      <Link to="/settings?tab=models" style={{
        background: "#92400e", color: "white", padding: "5px 12px",
        borderRadius: 6, fontSize: 12, fontWeight: 600, textDecoration: "none",
        whiteSpace: "nowrap",
      }}>
        {t("noModel.goFix")}
      </Link>
    </div>
  );
}
