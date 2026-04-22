import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { WorkspacesAPI, WorkspaceFile } from "../api/client";
import Markdown from "../components/Markdown";

function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function isMarkdown(path: string) {
  return path.toLowerCase().endsWith(".md");
}

export default function WorkspaceDetail() {
  const { t } = useTranslation();
  const { id } = useParams();
  const wid = Number(id);
  const qc = useQueryClient();

  const { data: ws } = useQuery({
    queryKey: ["workspace", wid],
    queryFn: () => WorkspacesAPI.get(wid),
    enabled: !!wid,
  });

  const { data: fileTree } = useQuery({
    queryKey: ["workspace", wid, "files"],
    queryFn: () => WorkspacesAPI.listFiles(wid),
    enabled: !!wid,
    refetchInterval: 5000,
  });

  const [selected, setSelected] = useState<string | null>(null);

  const { data: content } = useQuery({
    queryKey: ["workspace", wid, "file", selected],
    queryFn: () => selected ? WorkspacesAPI.readFile(wid, selected) : Promise.resolve(null),
    enabled: !!selected,
  });

  const del = useMutation({
    mutationFn: (relpath: string) => WorkspacesAPI.removeFile(wid, relpath),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", wid, "files"] });
      if (selected) {
        qc.invalidateQueries({ queryKey: ["workspace", wid, "file", selected] });
      }
      setSelected(null);
    },
  });

  if (!ws) {
    return <div className="page">{t("btn.loading")}</div>;
  }

  const files = (fileTree?.files ?? []).filter((f) => !f.is_dir);

  return (
    <div className="page">
      <div style={{ marginBottom: 14 }}>
        <Link to="/workspaces" style={{ fontSize: 11, color: "var(--ink-3)", textDecoration: "none" }}>
          ← {t("workspaces.back")}
        </Link>
      </div>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <h1 style={{ marginBottom: 4 }}>{ws.name}</h1>
          <div className="subtitle">
            {files.length} {t("workspaces.fileCountUnit")} · {fmtBytes(ws.size_bytes)}
            {" · "}{t("workspaces.storagePath")} <span style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}>{ws.storage_path}</span>
          </div>
        </div>
        <a
          data-testid={`workspace-download-${wid}`}
          href={WorkspacesAPI.downloadZipUrl(wid)}
          className="mbtn"
          style={{ fontSize: 12 }}
        >
          {t("workspaces.downloadZip")}
        </a>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "280px 1fr",
        gap: 14,
        marginTop: 18,
      }}>
        {/* File tree (flat list for simplicity) */}
        <div style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          padding: 10,
          height: "70vh",
          overflowY: "auto",
        }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--ink-3)", marginBottom: 6, letterSpacing: 1 }}>
            {t("workspaces.files").toUpperCase()}
          </div>
          {files.length === 0 && (
            <div style={{ fontSize: 11, color: "var(--ink-4)", padding: "20px 10px", textAlign: "center" }}>
              {t("workspaces.emptyTree")}
            </div>
          )}
          {files.map((f: WorkspaceFile) => {
            const active = selected === f.path;
            return (
              <button
                key={f.path}
                data-testid={`file-item-${f.path}`}
                onClick={() => setSelected(f.path)}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  padding: "6px 8px",
                  fontSize: 12,
                  fontFamily: "var(--font-mono)",
                  background: active ? "var(--accent-soft)" : "transparent",
                  color: active ? "var(--accent)" : "var(--ink-2)",
                  border: "none",
                  borderRadius: 6,
                  cursor: "pointer",
                  marginBottom: 1,
                }}
              >
                {f.path} <span style={{ float: "right", fontSize: 10, color: "var(--ink-4)" }}>
                  {fmtBytes(f.size)}
                </span>
              </button>
            );
          })}
        </div>

        {/* File viewer */}
        <div style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          padding: 16,
          height: "70vh",
          overflowY: "auto",
        }}>
          {!selected && (
            <div style={{ fontSize: 12, color: "var(--ink-4)", padding: 30, textAlign: "center" }}>
              {t("workspaces.pickFile")}
            </div>
          )}
          {selected && content && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <div style={{ fontSize: 13, fontWeight: 800, fontFamily: "var(--font-mono)", flex: 1, wordBreak: "break-all" }}>
                  {selected}
                </div>
                <button
                  data-testid={`file-delete-${selected}`}
                  onClick={() => {
                    if (confirm(t("workspaces.confirmDeleteFile", { path: selected }))) {
                      del.mutate(selected);
                    }
                  }}
                  className="mbtn danger"
                  style={{ fontSize: 11 }}
                >
                  {t("btn.delete")}
                </button>
              </div>
              {isMarkdown(selected) ? (
                <Markdown content={content.content} />
              ) : (
                <pre style={{
                  margin: 0,
                  fontSize: 12,
                  lineHeight: 1.5,
                  fontFamily: "var(--font-mono)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}>{content.content}</pre>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
