import { headUrl, AvatarConfig } from "../api/client";
import "./Avatar.css";

/**
 * Shared circular avatar. Always renders the head-focused viewBox with a
 * gray background, so the face stays centered inside the circle regardless
 * of size. Callers pass size in pixels.
 *
 * Use this component everywhere a small round headshot is needed — dashboard
 * cards, dialog cast members, agent lists, Run detail step icons, etc.
 * For the full-body bust (dialog focus-lane portrait), use `bustUrl()` directly.
 */
export default function Avatar({
  cfg,
  size = 36,
  title,
  className,
  bg = "eaeaea",
}: {
  cfg: AvatarConfig | Record<string, string> | undefined | null;
  size?: number;
  title?: string;
  className?: string;
  bg?: string;
}) {
  const src = headUrl(cfg || {}, bg);
  return (
    <div
      className={`avatar-round ${className ?? ""}`}
      style={{ width: size, height: size, background: `#${bg}` }}
      title={title}
    >
      <img src={src} alt={title || "avatar"} draggable={false} />
    </div>
  );
}
