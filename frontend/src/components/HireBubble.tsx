import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { HireProposal, LeadAPI } from "../api/client";
import Avatar from "./Avatar";

/**
 * Inline card rendered inside a Lead message bubble when Lead proposes
 * hiring a new agent. The admin sees the draft profile and can either:
 *   - Hire: create the agent immediately (with any field edits)
 *   - Pass: dismiss the proposal (handled at the conversation level —
 *           user just writes "nah" in the next turn)
 *
 * Once accepted, the proposal is replaced with a green confirmation row
 * showing the hired agent's name + role.
 */
export default function HireBubble({
  messageId,
  proposal,
  hiredAgentId,
}: {
  messageId: number;
  proposal: HireProposal;
  hiredAgentId?: number;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<HireProposal>(proposal);
  const [err, setErr] = useState("");

  const accept = useMutation({
    mutationFn: () =>
      LeadAPI.acceptHire(messageId, editing ? form : undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-messages"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["lead-thread-messages"] });
    },
    onError: (e: Error) => setErr(e.message),
  });

  // Already hired → confirmation state
  if (hiredAgentId) {
    return (
      <div className="hire-bubble hire-bubble--done">
        <span className="hire-check">✓</span>
        <span>
          {t("hireBubble.hiredPrefix")} <strong>{proposal.name}</strong> — {proposal.role_title}
        </span>
      </div>
    );
  }

  return (
    <div className="hire-bubble">
      <div className="hire-bubble-head">
        <Avatar
          cfg={{ body: "Shirt", hair: "Medium", face: "Calm" }}
          size={40}
          title={proposal.name}
        />
        <div className="hire-bubble-title">
          <div className="hire-bubble-name">
            {editing ? (
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            ) : (
              <strong>{proposal.name}</strong>
            )}
          </div>
          <div className="hire-bubble-role">
            {editing ? (
              <input
                value={form.role_title}
                onChange={(e) => setForm({ ...form, role_title: e.target.value })}
              />
            ) : (
              proposal.role_title
            )}
          </div>
        </div>
        <span className="hire-bubble-badge">{t("hireBubble.proposedBadge")}</span>
      </div>

      {proposal.description && !editing && (
        <div className="hire-bubble-desc">{proposal.description}</div>
      )}
      {editing && (
        <textarea
          className="hire-bubble-desc-edit"
          rows={2}
          value={form.description || ""}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          placeholder={t("hireBubble.descriptionPlaceholder")}
        />
      )}

      <details className="hire-bubble-prompt">
        <summary>{t("hireBubble.systemPromptLabel")}</summary>
        {editing ? (
          <textarea
            rows={8}
            value={form.system_prompt}
            onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
          />
        ) : (
          <pre>{proposal.system_prompt}</pre>
        )}
      </details>

      {proposal.rationale && (
        <div className="hire-bubble-rationale">
          <em>{t("hireBubble.whyLabel")} {proposal.rationale}</em>
        </div>
      )}

      {err && <div className="hire-bubble-err">{err}</div>}

      <div className="hire-bubble-actions">
        {editing ? (
          <>
            <button
              className="mbtn ghost"
              onClick={() => {
                setForm(proposal);
                setEditing(false);
              }}
            >
              {t("hireBubble.cancelEdits")}
            </button>
            <button
              className="mbtn primary"
              disabled={accept.isPending}
              onClick={() => accept.mutate()}
            >
              {accept.isPending ? t("hireBubble.hiring") : t("hireBubble.hireWithEdits")}
            </button>
          </>
        ) : (
          <>
            <button className="mbtn ghost" onClick={() => setEditing(true)}>
              {t("hireBubble.editBeforeHiring")}
            </button>
            <button
              className="mbtn primary"
              disabled={accept.isPending}
              onClick={() => accept.mutate()}
            >
              {accept.isPending ? t("hireBubble.hiring") : t("hireBubble.hire")}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
