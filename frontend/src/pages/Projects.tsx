import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AgentsAPI, Project, ProjectsAPI } from "../api/client";
import Avatar from "../components/Avatar";
import Modal from "../components/Modal";

export default function Projects() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data: projects = [] } = useQuery({
    queryKey: ["projects"],
    queryFn: () => ProjectsAPI.list(),
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [toDelete, setToDelete] = useState<Project | null>(null);

  const del = useMutation({
    mutationFn: (id: number) => ProjectsAPI.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setToDelete(null);
    },
  });

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 4 }}>
        <div>
          <h1>{t("projects.title")}</h1>
          <div className="subtitle">{t("projects.subtitle")}</div>
        </div>
        <button
          className="mbtn primary"
          onClick={() => setCreateOpen(true)}
          style={{ padding: "10px 18px", fontSize: 13, fontWeight: 800 }}
        >
          {t("projects.createNew")}
        </button>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
        gap: 16,
        marginTop: 20,
      }}>
        {projects.length === 0 && (
          <div style={{ gridColumn: "1 / -1", padding: 60, textAlign: "center", color: "var(--ink-4)" }}>
            {t("projects.empty")}
          </div>
        )}
        {projects.map((p) => (
          <div
            key={p.id}
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 16,
              padding: 18,
              boxShadow: "var(--shadow-sm)",
              cursor: "pointer",
            }}
            onClick={() => navigate(`/projects/${p.id}`)}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
              <div style={{ fontSize: 15, fontWeight: 800, flex: 1 }}>{p.name}</div>
              <StatusPill status={p.status} />
            </div>
            {p.description && (
              <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 10, lineHeight: 1.5 }}>
                {p.description}
              </div>
            )}
            <div style={{ fontSize: 11, color: "var(--ink-4)", display: "flex", gap: 10, marginBottom: 10 }}>
              <span>{p.member_count ?? 0} {t("common.members")}</span>
              <span>·</span>
              <span>{p.runs_count ?? 0} {t("common.runs")}</span>
              <span>·</span>
              <span>${Number(p.today_cost ?? 0).toFixed(2)} {t("projects.todayCost")}</span>
            </div>
            <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }} onClick={(e) => e.stopPropagation()}>
              <button
                className="mbtn"
                onClick={() => navigate(`/projects/${p.id}`)}
                style={{ padding: "6px 12px", fontSize: 11 }}
              >
                {t("btn.open")}
              </button>
              <button
                className="mbtn danger"
                onClick={() => setToDelete(p)}
                style={{ padding: "6px 12px", fontSize: 11 }}
              >
                {t("btn.delete")}
              </button>
            </div>
          </div>
        ))}
      </div>

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSaved={(id) => {
          qc.invalidateQueries({ queryKey: ["projects"] });
          setCreateOpen(false);
          navigate(`/projects/${id}`);
        }}
      />

      <Modal
        open={!!toDelete}
        onClose={() => setToDelete(null)}
        title={t("projects.deleteTitle")}
        subtitle={toDelete?.name || ""}
        size="sm"
        footer={
          <>
            <button className="mbtn" onClick={() => setToDelete(null)} disabled={del.isPending}>{t("btn.cancel")}</button>
            <button
              className="mbtn danger"
              onClick={() => toDelete && del.mutate(toDelete.id)}
              disabled={del.isPending}
            >
              {del.isPending ? t("projects.deleting") : t("btn.delete")}
            </button>
          </>
        }
      >
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          {t("projects.deleteConfirmDesc")}
        </div>
      </Modal>
    </div>
  );
}


function StatusPill({ status }: { status: Project["status"] }) {
  const color = {
    active: { bg: "var(--accent-soft)", fg: "var(--accent)" },
    paused: { bg: "var(--surface-2)", fg: "var(--ink-3)" },
    done:   { bg: "#e7f5ed", fg: "#0a7a41" },
    archived: { bg: "var(--surface-2)", fg: "var(--ink-4)" },
  }[status] || { bg: "var(--surface-2)", fg: "var(--ink-3)" };
  return (
    <span style={{
      fontSize: 9, fontWeight: 800, letterSpacing: 1.2,
      background: color.bg, color: color.fg,
      padding: "2px 8px", borderRadius: 999,
      textTransform: "uppercase",
    }}>{status}</span>
  );
}


function CreateProjectModal({
  open, onClose, onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (id: number) => void;
}) {
  const { t } = useTranslation();
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: AgentsAPI.list });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [goal, setGoal] = useState("");
  const [memberIds, setMemberIds] = useState<number[]>([]);
  const [coordinatorId, setCoordinatorId] = useState<number | null>(null);
  const [alloc, setAlloc] = useState<Record<number, number>>({});

  useEffect(() => {
    if (!open) {
      setName(""); setDescription(""); setGoal("");
      setMemberIds([]); setCoordinatorId(null); setAlloc({});
    }
  }, [open]);

  const save = useMutation({
    mutationFn: async () => {
      const members = memberIds.map((id) => ({
        agent_id: id,
        daily_alloc_pct: alloc[id] ?? 100,
        monthly_alloc_pct: 100,
      }));
      return ProjectsAPI.create({
        name, description, goal,
        members,
        coordinator_agent_id: coordinatorId,
      });
    },
    onSuccess: (res) => onSaved(res.id),
  });

  const toggleMember = (id: number) => {
    setMemberIds((s) => s.includes(id) ? s.filter((x) => x !== id) : [...s, id]);
    if (!alloc[id]) setAlloc({ ...alloc, [id]: 100 });
  };

  const canSubmit = name.trim().length > 0 && memberIds.length > 0 && !save.isPending;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("projects.createTitle")}
      subtitle={t("projects.createSubtitle")}
      size="lg"
      footer={
        <>
          <button className="mbtn" onClick={onClose} disabled={save.isPending}>{t("btn.cancel")}</button>
          <button className="mbtn primary" onClick={() => save.mutate()} disabled={!canSubmit}>
            {save.isPending ? t("projects.creating") : t("projects.createSubmit")}
          </button>
        </>
      }
    >
      <div className="modal-field">
        <label>{t("groups.name")}</label>
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus
               placeholder={t("projects.namePlaceholder")} />
      </div>
      <div className="modal-field">
        <label>{t("groups.descLabel")}</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)}
                  style={{ minHeight: 50 }} />
      </div>
      <div className="modal-field">
        <label>{t("projects.goal")}</label>
        <textarea value={goal} onChange={(e) => setGoal(e.target.value)}
                  placeholder={t("projects.goalPlaceholder")} style={{ minHeight: 50 }} />
      </div>

      <div className="modal-field">
        <label>{t("projects.membersAllocationLabel")}</label>
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
          gap: 8, maxHeight: 340, overflowY: "auto",
        }}>
          {agents.filter((a) => !a.is_lead).map((a) => {
            const selected = memberIds.includes(a.id);
            return (
              <div key={a.id} style={{
                padding: 8,
                background: selected ? "var(--accent-soft)" : "white",
                border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                borderRadius: 10,
              }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer" }}
                     onClick={() => toggleMember(a.id)}>
                  <Avatar cfg={a.avatar_config} size={32} title={a.name} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, fontWeight: 800 }}>{a.name}</div>
                    <div style={{ fontSize: 10, color: "var(--ink-3)" }}>{a.role_title || ""}</div>
                  </div>
                  <input type="checkbox" checked={selected} readOnly />
                </div>
                {selected && (
                  <div style={{ marginTop: 6 }}>
                    <input
                      type="range" min={10} max={100} step={5}
                      value={alloc[a.id] ?? 100}
                      onChange={(e) => setAlloc({ ...alloc, [a.id]: Number(e.target.value) })}
                      style={{ width: "100%" }}
                    />
                    <div style={{ fontSize: 10, color: "var(--ink-3)", textAlign: "right" }}>
                      {alloc[a.id] ?? 100}%
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="modal-field">
        <label>{t("projects.coordinatorLabel")}</label>
        <select value={coordinatorId ?? ""}
                onChange={(e) => setCoordinatorId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">—</option>
          {agents.filter((a) => memberIds.includes(a.id)).map((a) => (
            <option key={a.id} value={a.id}>{a.name} · {a.role_title}</option>
          ))}
        </select>
        <div className="hint">{t("projects.coordinatorHint")}</div>
      </div>
    </Modal>
  );
}
