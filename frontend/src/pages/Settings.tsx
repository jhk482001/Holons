import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { useIsAdmin } from "../auth";
import PersonalTab from "../components/settings/PersonalTab";
import UserManagementTab from "../components/settings/UserManagementTab";
import SystemSettingsTab from "../components/settings/SystemSettingsTab";
import ModelClientsTab from "../components/settings/ModelClientsTab";
import ChannelsTab from "../components/settings/ChannelsTab";
import "./Records.css"; // reuse .page-tabs + suppression rules

type TabKey = "personal" | "channels" | "users" | "system" | "models";

export default function Settings() {
  const { t } = useTranslation();
  const isAdmin = useIsAdmin();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab") as TabKey | null;

  // Non-admins landing on admin-only tabs get bounced to personal.
  const visibleTabs: { key: TabKey; label: string }[] = [
    { key: "personal", label: t("settings.tab.personal") },
    { key: "channels", label: t("settings.tab.channels") },
    ...(isAdmin
      ? ([
          { key: "users" as TabKey, label: t("settings.tab.users") },
          { key: "models" as TabKey, label: t("settings.tab.models") },
          { key: "system" as TabKey, label: t("settings.tab.system") },
        ])
      : []),
  ];
  const tab: TabKey = visibleTabs.some((t) => t.key === raw)
    ? (raw as TabKey)
    : "personal";

  function setTab(k: TabKey) {
    const next = new URLSearchParams(params);
    next.set("tab", k);
    setParams(next, { replace: true });
  }

  return (
    <div className="page settings-page">
      <h1>{t("settings.title")}</h1>
      <div className="subtitle">
        {isAdmin ? t("settings.subtitleAdmin") : t("settings.subtitleUser")}
      </div>

      <nav className="page-tabs" data-testid="settings-tabs">
        {visibleTabs.map((t) => (
          <button
            key={t.key}
            className={`page-tab ${tab === t.key ? "active" : ""}`}
            data-testid={`settings-tab-${t.key}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="settings-tab-body">
        {tab === "personal" && <PersonalTab />}
        {tab === "channels" && <ChannelsTab />}
        {tab === "users" && isAdmin && <UserManagementTab />}
        {tab === "models" && isAdmin && <ModelClientsTab />}
        {tab === "system" && isAdmin && <SystemSettingsTab />}
      </div>
    </div>
  );
}
