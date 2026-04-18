import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AvatarAPI, AvatarConfig, bustUrl, thumbUrl } from "../api/client";
import "./AvatarBuilder.css";

interface Props {
  value: AvatarConfig;
  onChange: (cfg: AvatarConfig) => void;
}

const CATEGORY_ORDER = ["body_bust", "hair", "face", "facial_hair", "accessory"] as const;
type Category = (typeof CATEGORY_ORDER)[number];

const CAT_LABEL_KEYS: Record<Category, string> = {
  body_bust: "avatar.body",
  hair: "avatar.hair",
  face: "avatar.face",
  facial_hair: "avatar.facialHair",
  accessory: "avatar.accessory",
};

// Which state field does each category map to
const CAT_TO_FIELD: Record<Category, keyof AvatarConfig> = {
  body_bust: "body",
  hair: "hair",
  face: "face",
  facial_hair: "facial_hair",
  accessory: "accessory",
};

export default function AvatarBuilder({ value, onChange }: Props) {
  const { t } = useTranslation();
  const [activeCat, setActiveCat] = useState<Category>("body_bust");

  const { data: parts = {}, isLoading } = useQuery({
    queryKey: ["avatar-parts"],
    queryFn: AvatarAPI.parts,
    staleTime: 5 * 60 * 1000,
  });

  const cfg: AvatarConfig = {
    body_type: "body_bust",
    body: value.body || "Shirt",
    hair: value.hair || "Medium",
    face: value.face || "Calm",
    facial_hair: value.facial_hair,
    accessory: value.accessory,
    ...value,
  };

  const randomize = () => {
    const pick = (c: string) => {
      const list = parts[c]?.parts ?? [];
      return list.length ? list[Math.floor(Math.random() * list.length)] : undefined;
    };
    onChange({
      ...cfg,
      body: pick("body_bust") || "Shirt",
      hair: pick("hair") || "Medium",
      face: pick("face") || "Calm",
      facial_hair: Math.random() > 0.6 ? pick("facial_hair") : undefined,
      accessory: Math.random() > 0.6 ? pick("accessory") : undefined,
    });
  };

  const select = (cat: Category, name: string | undefined) => {
    const field = CAT_TO_FIELD[cat];
    onChange({ ...cfg, [field]: name } as AvatarConfig);
  };

  const activeParts = parts[activeCat]?.parts ?? [];
  const optional = parts[activeCat]?.required === false;
  const currentVal = cfg[CAT_TO_FIELD[activeCat]] as string | undefined;

  if (isLoading) {
    return <div className="avatar-builder loading">{t("avatar.loadingParts")}</div>;
  }

  return (
    <div className="avatar-builder">
      <div className="avatar-preview">
        <img src={bustUrl(cfg, true)} alt="avatar preview" />
      </div>

      <div className="avatar-controls">
        <div className="avatar-tabs">
          {CATEGORY_ORDER.map((cat) => {
            const count = parts[cat]?.parts.length ?? 0;
            return (
              <button
                key={cat}
                type="button"
                className={activeCat === cat ? "active" : ""}
                onClick={() => setActiveCat(cat)}
              >
                {t(CAT_LABEL_KEYS[cat])}
                <span className="count">{count}</span>
              </button>
            );
          })}
        </div>

        <div className="avatar-grid">
          {optional && (
            <div
              className={`part-card none ${!currentVal ? "selected" : ""}`}
              onClick={() => select(activeCat, undefined)}
              title={t("avatar.none")}
            >
              ✕
            </div>
          )}
          {activeParts.map((name) => (
            <div
              key={name}
              className={`part-card ${currentVal === name ? "selected" : ""}`}
              onClick={() => select(activeCat, name)}
              title={name}
            >
              <img src={thumbUrl(activeCat, name)} alt={name} loading="lazy" />
            </div>
          ))}
        </div>

        <div className="avatar-footer">
          <button type="button" className="btn-random" onClick={randomize}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <path d="M16 8h.01M8 8h.01M12 12h.01M8 16h.01M16 16h.01" />
            </svg>
            {t("avatar.random")}
          </button>
        </div>
      </div>
    </div>
  );
}
