import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AgentsAPI, Group, GroupsAPI } from "../api/client";
import Avatar from "../components/Avatar";
import Modal from "../components/Modal";

export default function Groups() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data: groups = [] } = useQuery({ queryKey: ["groups"], queryFn: GroupsAPI.list });
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: AgentsAPI.list });

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Group | null>(null);
  const [toDelete, setToDelete] = useState<Group | null>(null);

  const del = useMutation({
    mutationFn: (id: number) => GroupsAPI.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["groups"] });
      setToDelete(null);
    },
  });

  const agentById = (id: number | null) => agents.find((a) => a.id === id);

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 4 }}>
        <div>
          <h1>{t("groups.title")}</h1>
          <div className="subtitle">{t("groups.subtitle")}</div>
        </div>
        <button
          data-testid="new-group-btn"
          onClick={() => setCreateOpen(true)}
          style={{
            padding: "10px 18px",
            background: "var(--accent)",
            color: "white",
            border: "1px solid var(--accent)",
            borderRadius: 10,
            fontSize: 13,
            fontWeight: 800,
            cursor: "pointer",
          }}
        >
          {t("groups.createNew")}
        </button>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
        gap: 16,
        marginTop: 20,
      }}>
        {groups.length === 0 && (
          <div style={{ gridColumn: "1 / -1", padding: 60, textAlign: "center", color: "var(--ink-4)" }}>
            {t("groups.empty")}
          </div>
        )}
        {groups.map((g) => {
          const aggregator = agentById(g.aggregator_agent_id);
          return (
            <div
              key={g.id}
              data-testid={`group-card-${g.id}`}
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 16,
                padding: 18,
                boxShadow: "var(--shadow-sm)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <div style={{ fontSize: 15, fontWeight: 800 }}>{g.name}</div>
                <span style={{
                  fontSize: 9,
                  fontWeight: 800,
                  letterSpacing: 1.2,
                  background: g.mode === "parallel" ? "var(--accent-soft)" : "var(--surface-2)",
                  color: g.mode === "parallel" ? "var(--accent)" : "var(--ink-3)",
                  padding: "2px 8px",
                  borderRadius: 999,
                }}>
                  {g.mode === "parallel" ? t("groups.parallel") : t("groups.sequential")}
                </span>
              </div>
              {g.description && (
                <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 10 }}>
                  {g.description}
                </div>
              )}
              <div style={{ fontSize: 11, color: "var(--ink-4)", marginBottom: 10 }}>
                {t("groups.members", { count: g.member_count ?? 0 })}
                {aggregator && <> · {t("groups.aggregator")}<strong style={{ color: "var(--ink-2)" }}>{aggregator.name}</strong></>}
              </div>
              <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                <button
                  className="mbtn primary"
                  data-testid={`chat-group-${g.id}`}
                  onClick={() => navigate(`/group-chat/${g.id}`)}
                  style={{ padding: "6px 12px", fontSize: 11 }}
                >
                  {t("groups.openChat")}
                </button>
                <button
                  className="mbtn"
                  data-testid={`edit-group-${g.id}`}
                  onClick={() => setEditing(g)}
                  style={{ padding: "6px 12px", fontSize: 11 }}
                >
                  {t("groups.edit")}
                </button>
                <button
                  className="mbtn danger"
                  data-testid={`delete-group-${g.id}`}
                  onClick={() => setToDelete(g)}
                  style={{ padding: "6px 12px", fontSize: 11 }}
                >
                  {t("groups.delete")}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <GroupFormModal
        open={createOpen || !!editing}
        editing={editing}
        onClose={() => {
          setCreateOpen(false);
          setEditing(null);
        }}
        onSaved={() => {
          qc.invalidateQueries({ queryKey: ["groups"] });
          setCreateOpen(false);
          setEditing(null);
        }}
      />

      <Modal
        open={!!toDelete}
        onClose={() => setToDelete(null)}
        title={t("groups.deleteTitle")}
        subtitle={toDelete?.name || ""}
        size="sm"
        footer={
          <>
            <button className="mbtn" onClick={() => setToDelete(null)} disabled={del.isPending}>{t("btn.cancel")}</button>
            <button
              className="mbtn danger"
              data-testid="confirm-delete-group"
              onClick={() => toDelete && del.mutate(toDelete.id)}
              disabled={del.isPending}
            >
              {del.isPending ? t("agentDetail.deleting") : t("agentDetail.deleteSubmit")}
            </button>
          </>
        }
      >
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          {t("groups.deleteDesc")}
        </div>
      </Modal>
    </div>
  );
}

function GroupFormModal({
  open,
  editing,
  onClose,
  onSaved,
}: {
  open: boolean;
  editing: Group | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: AgentsAPI.list });
  const { data: fullGroup } = useQuery({
    queryKey: ["group", editing?.id],
    queryFn: () => GroupsAPI.get(editing!.id),
    enabled: !!editing && open,
  });

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [mode, setMode] = useState<"parallel" | "sequential">("parallel");
  const [aggregatorId, setAggregatorId] = useState<number | null>(null);
  const [memberIds, setMemberIds] = useState<number[]>([]);

  useEffect(() => {
    if (!open) return;
    if (editing && fullGroup) {
      setName(fullGroup.name);
      setDescription(fullGroup.description || "");
      setMode(fullGroup.mode);
      setAggregatorId(fullGroup.aggregator_agent_id);
      setMemberIds((fullGroup.members || []).map((m) => m.agent_id));
    } else if (!editing) {
      setName("");
      setDescription("");
      setMode("parallel");
      setAggregatorId(null);
      setMemberIds([]);
    }
  }, [open, editing, fullGroup]);

  const save = useMutation({
    mutationFn: async () => {
      const payload = {
        name,
        description,
        mode,
        aggregator_agent_id: aggregatorId,
        member_agent_ids: memberIds,
      };
      if (editing) {
        await GroupsAPI.update(editing.id, payload);
      } else {
        await GroupsAPI.create(payload);
      }
    },
    onSuccess: onSaved,
  });

  const toggleMember = (id: number) => {
    setMemberIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]
    );
  };

  const canSubmit = name.trim().length > 0 && memberIds.length > 0 && !save.isPending;
  const nonLeadAgents = agents.filter((a) => !a.is_lead);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? t("groups.editTitle") : t("groups.createTitle")}
      subtitle={t("groups.formSubtitle")}
      size="lg"
      footer={
        <>
          <button className="mbtn" onClick={onClose} disabled={save.isPending}>{t("btn.cancel")}</button>
          <button
            className="mbtn primary"
            data-testid="save-group-submit"
            onClick={() => save.mutate()}
            disabled={!canSubmit}
          >
            {save.isPending ? t("btn.saving") : editing ? t("btn.save") : t("groups.createSubmit")}
          </button>
        </>
      }
    >
      <div className="modal-field">
        <label>{t("groups.name")}</label>
        <input
          data-testid="group-name-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("groups.namePlaceholder")}
          autoFocus
        />
      </div>
      <div className="modal-field">
        <label>{t("groups.descLabel")}</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          style={{ minHeight: 50 }}
        />
      </div>
      <div className="modal-field">
        <label>{t("groups.execMode")}</label>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className={`mbtn ${mode === "parallel" ? "primary" : ""}`}
            onClick={() => setMode("parallel")}
            style={{ flex: 1 }}
          >
            {t("groups.parallelMode")}
          </button>
          <button
            type="button"
            className={`mbtn ${mode === "sequential" ? "primary" : ""}`}
            onClick={() => setMode("sequential")}
            style={{ flex: 1 }}
          >
            {t("groups.sequentialMode")}
          </button>
        </div>
      </div>
      <div className="modal-field">
        <label>{t("groups.membersLabel", { count: memberIds.length })}</label>
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
          gap: 8,
          maxHeight: 240,
          overflowY: "auto",
        }}>
          {nonLeadAgents.map((a) => {
            const selected = memberIds.includes(a.id);
            return (
              <button
                key={a.id}
                type="button"
                data-testid={`group-member-toggle-${a.id}`}
                onClick={() => toggleMember(a.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: 8,
                  background: selected ? "var(--accent-soft)" : "white",
                  border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                  borderRadius: 10,
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <Avatar cfg={a.avatar_config} size={32} title={a.name} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 800 }}>{a.name}</div>
                  <div style={{ fontSize: 9, color: "var(--ink-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {a.role_title || "—"}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
      <div className="modal-field">
        <label>{t("groups.aggregatorLabel")}</label>
        <select
          value={aggregatorId ?? ""}
          onChange={(e) => setAggregatorId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">—</option>
          {nonLeadAgents.map((a) => (
            <option key={a.id} value={a.id}>{a.name} · {a.role_title}</option>
          ))}
        </select>
        <div className="hint">{t("groups.aggregatorHint")}</div>
      </div>
    </Modal>
  );
}
