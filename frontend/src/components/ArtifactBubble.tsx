import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Artifact } from "../api/client";
import "./ArtifactBubble.css";

/**
 * Four artifact kinds rendered as dedicated bubbles inside a Lead message:
 *   - html     → sandboxed iframe with the agent-authored HTML page
 *   - slides   → same, but labelled as a deck + "Open fullscreen" action
 *   - markdown → rendered Markdown (GFM — tables, task lists, strikethrough)
 *   - file     → download chip with filename, mime, size, click-to-save
 *
 * html/slides are isolated from the parent page via `iframe.sandbox`;
 * markdown is rendered inline since the source is plain text we control
 * via react-markdown (no raw HTML pass-through, so no XSS surface).
 */
export default function ArtifactBubble({ artifact }: { artifact: Artifact }) {
  if (artifact.kind === "html") return <HtmlBubble artifact={artifact} />;
  if (artifact.kind === "slides") return <SlidesBubble artifact={artifact} />;
  if (artifact.kind === "markdown") return <MarkdownBubble artifact={artifact} />;
  if (artifact.kind === "file") return <FileBubble artifact={artifact} />;
  return null;
}


function HtmlBubble({ artifact }: {
  artifact: Extract<Artifact, { kind: "html" }>;
}) {
  const [full, setFull] = useState(false);
  return (
    <div className={`artifact-bubble artifact-html ${full ? "fullscreen" : ""}`}>
      <div className="artifact-head">
        <span className="artifact-badge">HTML prototype</span>
        <span className="artifact-title">{artifact.title || "Untitled"}</span>
        <span className="artifact-actions">
          <button className="artifact-btn" onClick={() => setFull((f) => !f)}>
            {full ? "Exit fullscreen" : "Fullscreen"}
          </button>
          <a
            className="artifact-btn"
            href={`data:text/html;charset=utf-8,${encodeURIComponent(artifact.html)}`}
            download={(artifact.title || "prototype") + ".html"}
          >
            Download
          </a>
        </span>
      </div>
      {/* sandbox="allow-scripts" blocks same-origin (no reading parent
          cookies), allow-same-origin is deliberately omitted. The
          iframe can still run its own inline JS. */}
      <iframe
        title={artifact.title || "HTML prototype"}
        sandbox="allow-scripts"
        srcDoc={artifact.html}
        className="artifact-iframe"
      />
    </div>
  );
}


function SlidesBubble({ artifact }: {
  artifact: Extract<Artifact, { kind: "slides" }>;
}) {
  const [full, setFull] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  return (
    <div className={`artifact-bubble artifact-slides ${full ? "fullscreen" : ""}`}>
      <div className="artifact-head">
        <span className="artifact-badge">Slide deck</span>
        <span className="artifact-title">{artifact.title || "Untitled"}</span>
        <span className="artifact-actions">
          <button className="artifact-btn" onClick={() => setFull((f) => !f)}>
            {full ? "Exit fullscreen" : "Fullscreen"}
          </button>
          <a
            className="artifact-btn"
            href={`data:text/html;charset=utf-8,${encodeURIComponent(artifact.html)}`}
            download={(artifact.title || "deck") + ".html"}
          >
            Download
          </a>
        </span>
      </div>
      <iframe
        ref={iframeRef}
        title={artifact.title || "Slide deck"}
        sandbox="allow-scripts"
        srcDoc={artifact.html}
        className="artifact-iframe artifact-iframe-slides"
      />
    </div>
  );
}


function MarkdownBubble({ artifact }: {
  artifact: Extract<Artifact, { kind: "markdown" }>;
}) {
  const [full, setFull] = useState(false);
  return (
    <div className={`artifact-bubble artifact-markdown ${full ? "fullscreen" : ""}`}>
      <div className="artifact-head">
        <span className="artifact-badge">Markdown</span>
        <span className="artifact-title">{artifact.title || "Untitled"}</span>
        <span className="artifact-actions">
          <button className="artifact-btn" onClick={() => setFull((f) => !f)}>
            {full ? "Exit fullscreen" : "Fullscreen"}
          </button>
          <a
            className="artifact-btn"
            href={`data:text/markdown;charset=utf-8,${encodeURIComponent(artifact.markdown)}`}
            download={(artifact.title || "document") + ".md"}
          >
            Download
          </a>
        </span>
      </div>
      <div className="artifact-md-body">
        {/* react-markdown sanitises by default — raw HTML in the source is
            escaped. GFM plugin enables tables, task lists, strikethrough. */}
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{artifact.markdown}</ReactMarkdown>
      </div>
    </div>
  );
}


function FileBubble({ artifact }: {
  artifact: Extract<Artifact, { kind: "file" }>;
}) {
  // Build a data URL the browser can download directly. utf-8 text files
  // get URL-encoded; base64 payloads are passed through as-is.
  const dataUrl =
    artifact.encoding === "base64"
      ? `data:${artifact.mime};base64,${artifact.content}`
      : `data:${artifact.mime};charset=utf-8,${encodeURIComponent(artifact.content)}`;

  // Approx size — utf-8 byte count for text, decoded byte count for base64.
  const sizeBytes =
    artifact.encoding === "base64"
      ? Math.floor((artifact.content.length * 3) / 4)
      : new Blob([artifact.content]).size;
  const sizeLabel = humanBytes(sizeBytes);

  return (
    <div className="artifact-bubble artifact-file">
      <span className="artifact-file-icon">📄</span>
      <span className="artifact-file-meta">
        <span className="artifact-file-name">{artifact.filename}</span>
        <span className="artifact-file-sub">
          {artifact.mime} · {sizeLabel}
        </span>
      </span>
      <a className="artifact-btn primary" href={dataUrl} download={artifact.filename}>
        Download
      </a>
    </div>
  );
}


function humanBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}
