import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";

interface SearchResult {
  query: string;
  threads: Array<{ thread_id: string; title: string | null; updated_at: string }>;
  runs: Array<{ id: number; workflow_id: number; status: string; started_at: string; snippet: string }>;
  reports: Array<{ id: number; project_id: number; report_date: string; project_name: string; snippet: string }>;
}

export default function Search() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");

  const { data } = useQuery({
    queryKey: ["search", params.get("q") || ""],
    queryFn: () => api.get<SearchResult>(`/search?q=${encodeURIComponent(params.get("q") || "")}`),
    enabled: !!params.get("q"),
  });

  const submit = () => {
    setParams(q.trim() ? { q: q.trim() } : {});
  };

  const total = (data?.threads.length ?? 0) + (data?.runs.length ?? 0) + (data?.reports.length ?? 0);

  return (
    <div className="page">
      <h1>{t("search.title")}</h1>
      <div className="subtitle">{t("search.subtitle")}</div>

      <div style={{ display: "flex", gap: 8, marginTop: 16, marginBottom: 20 }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          placeholder={t("search.placeholder")}
          autoFocus
          style={{ flex: 1, padding: 10, fontSize: 14, border: "1px solid var(--border)", borderRadius: 8 }}
        />
        <button className="mbtn primary" onClick={submit} disabled={!q.trim()}>
          {t("search.go")}
        </button>
      </div>

      {data && params.get("q") && (
        <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 16 }}>
          {t("search.results", { total, query: data.query })}
        </div>
      )}

      {data && data.threads.length > 0 && (
        <Section title={t("search.threads")}>
          {data.threads.map((t) => (
            <Row key={t.thread_id} onClick={() => navigate("/dialog")}
                 title={t.title || t.thread_id}
                 date={new Date(t.updated_at).toLocaleString()} />
          ))}
        </Section>
      )}
      {data && data.runs.length > 0 && (
        <Section title={t("search.runs")}>
          {data.runs.map((r) => (
            <Row key={r.id} onClick={() => navigate(`/runs/${r.id}`)}
                 title={`Run #${r.id} · ${r.status}`}
                 snippet={r.snippet}
                 date={new Date(r.started_at).toLocaleString()} />
          ))}
        </Section>
      )}
      {data && data.reports.length > 0 && (
        <Section title={t("search.reports")}>
          {data.reports.map((r) => (
            <Row key={r.id} onClick={() => navigate(`/projects/${r.project_id}`)}
                 title={`${r.project_name} · ${r.report_date}`}
                 snippet={r.snippet} />
          ))}
        </Section>
      )}
      {data && total === 0 && params.get("q") && (
        <div style={{ color: "var(--ink-4)", fontSize: 13, padding: 40, textAlign: "center" }}>
          {t("search.empty")}
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 24 }}>
      <h3 style={{ fontSize: 11, textTransform: "uppercase", color: "var(--ink-3)",
                   letterSpacing: 1, fontWeight: 800, marginBottom: 10 }}>{title}</h3>
      {children}
    </section>
  );
}

function Row({ title, snippet, date, onClick }: {
  title: string; snippet?: string; date?: string; onClick: () => void;
}) {
  return (
    <div onClick={onClick} style={{
      padding: "10px 14px", background: "var(--surface)",
      border: "1px solid var(--border)", borderRadius: 8, marginBottom: 6,
      cursor: "pointer",
    }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>{title}</div>
        {date && <div style={{ fontSize: 10, color: "var(--ink-4)", marginLeft: "auto" }}>{date}</div>}
      </div>
      {snippet && (
        <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4, lineHeight: 1.5 }}>
          …{snippet}…
        </div>
      )}
    </div>
  );
}
