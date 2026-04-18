import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./Markdown.css";

/**
 * Opinionated Markdown renderer used everywhere in the app that displays
 * LLM-generated text (DialogCenter messages, RunDetail step outputs, run
 * final output, lead proxy answers). Enables GitHub-flavored markdown
 * (tables, strikethrough, task lists) and wires up the common CSS hooks.
 *
 * The wrapper always applies `.md` which scopes the style rules defined
 * in Markdown.css, so callers don't need to remember to set it.
 */
export default function Markdown({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  return (
    <div className={`md ${className ?? ""}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}
