import { useTranslation } from "react-i18next";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { WorkspacesAPI, Workspace } from "../api/client";
import Modal from "../components/Modal";

function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export default function Workspaces() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: workspaces = [] } = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => WorkspacesAPI.list(),
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [toDelete, setToDelete] = useState<Workspace | null>(null);

  const create = useMutation({
    mutationFn: (name: string) => WorkspacesAPI.create({ name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      setCreateOpen(false);
    },
  });

  const del = useMutation({
    mutationFn: (id: number) => WorkspacesAPI.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      setToDelete(null);
    },
  });

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 4 }}>
        <div>
          <h1>{t("workspaces.title")}</h1>
          <div className="subtitle">{t("workspaces.subtitle")}</div>
        </div>
        <button
          data-testid="new-workspace-btn"
          onClick={() => setCreateOpen(true)}
          className="mbtn primary"
        >
          {t("workspaces.createNew")}
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 20 }}>
        {workspaces.map((w) => (
          <Link
            key={w.id}
            to={`/workspaces/${w.id}`}
            data-testid={`workspace-row-${w.id}`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "12px 16px",
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              textDecoration: "none",
              color: "inherit",
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 800 }}>{w.name}</div>
              {w.description && (
                <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>
                  {w.description}
                </div>
              )}
              <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 4 }}>
                {fmtBytes(w.size_bytes)} · {t("workspaces.updatedAt")} {new Date(w.updated_at).toLocaleString()}
              </div>
            </div>
            <button
              data-testid={`delete-workspace-${w.id}`}
              onClick={(e) => { e.preventDefault(); setToDelete(w); }}
              className="mbtn danger"
              style={{ fontSize: 11 }}
            >
              {t("btn.delete")}
            </button>
          </Link>
        ))}
        {workspaces.length === 0 && (
          <div style={{ textAlign: "center", color: "var(--ink-4)", padding: 60, fontSize: 13 }}>
            {t("workspaces.empty")}
          </div>
        )}
      </div>

      <CreateWorkspaceModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSubmit={(name) => create.mutate(name)}
        pending={create.isPending}
      />

      <Modal
        open={!!toDelete}
        onClose={() => setToDelete(null)}
        title={t("workspaces.deleteTitle")}
        subtitle={toDelete?.name || ""}
        size="sm"
        footer={
          <>
            <button className="mbtn" onClick={() => setToDelete(null)} disabled={del.isPending}>
              {t("btn.cancel")}
            </button>
            <button
              className="mbtn danger"
              data-testid="confirm-delete-workspace"
              onClick={() => toDelete && del.mutate(toDelete.id)}
              disabled={del.isPending}
            >
              {del.isPending ? t("btn.deleting") : t("btn.delete")}
            </button>
          </>
        }
      >
        <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.6 }}>
          {t("workspaces.deleteBody")}
        </div>
      </Modal>
    </div>
  );
}


function CreateWorkspaceModal({
  open,
  onClose,
  onSubmit,
  pending,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (name: string) => void;
  pending: boolean;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("workspaces.createTitle")}
      size="sm"
      footer={
        <>
          <button className="mbtn" onClick={onClose} disabled={pending}>{t("btn.cancel")}</button>
          <button
            className="mbtn primary"
            data-testid="create-workspace-submit"
            onClick={() => onSubmit(name.trim())}
            disabled={!name.trim() || pending}
          >
            {pending ? t("btn.creating") : t("btn.create")}
          </button>
        </>
      }
    >
      <div className="modal-field">
        <label>{t("workspaces.nameLabel")}</label>
        <input
          data-testid="new-workspace-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("workspaces.namePlaceholder")}
          autoFocus
        />
      </div>
    </Modal>
  );
}
