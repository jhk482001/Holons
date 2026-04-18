import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import Workflows from "./Workflows";
import Schedules from "./Schedules";
import "./Records.css"; // reuses the same .page-tabs + suppression rules

type TabKey = "workflows" | "schedules";

const TABS: { key: TabKey; labelKey: string }[] = [
  { key: "workflows", labelKey: "automation.tabWorkflows" },
  { key: "schedules", labelKey: "automation.tabSchedules" },
];

export default function Automation() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab") as TabKey | null;
  const tab: TabKey = (raw && TABS.some((tb) => tb.key === raw)) ? raw : "workflows";

  function setTab(k: TabKey) {
    const next = new URLSearchParams(params);
    next.set("tab", k);
    setParams(next, { replace: true });
  }

  return (
    <div className="page automation-page">
      <h1>{t("automation.title")}</h1>
      <div className="subtitle">{t("automation.subtitle")}</div>

      <nav className="page-tabs" data-testid="automation-tabs">
        {TABS.map((tb) => (
          <button
            key={tb.key}
            className={`page-tab ${tab === tb.key ? "active" : ""}`}
            data-testid={`automation-tab-${tb.key}`}
            onClick={() => setTab(tb.key)}
          >
            {t(tb.labelKey)}
          </button>
        ))}
      </nav>

      <div className="automation-tab-body">
        {tab === "workflows" && <Workflows />}
        {tab === "schedules" && <Schedules />}
      </div>
    </div>
  );
}
