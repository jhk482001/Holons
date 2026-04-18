import { useTranslation } from "react-i18next";
import { saveLang, DesktopLang } from "./api-adapter";

export default function LangSwitcher() {
  const { i18n } = useTranslation();
  const current = (i18n.language || "en") as DesktopLang;

  function switchTo(next: DesktopLang) {
    if (next === current) return;
    i18n.changeLanguage(next);
    saveLang(next);
  }

  return (
    <div className="lang-switcher" data-interactive>
      <button
        type="button"
        className={`lang-btn ${current === "en" ? "active" : ""}`}
        onClick={() => switchTo("en")}
      >
        EN
      </button>
      <span className="lang-sep">·</span>
      <button
        type="button"
        className={`lang-btn ${current === "zh-TW" ? "active" : ""}`}
        onClick={() => switchTo("zh-TW")}
      >
        中文
      </button>
    </div>
  );
}
