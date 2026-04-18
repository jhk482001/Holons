import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";
import zhTW from "./zh-TW.json";

export type DesktopLang = "en" | "zh-TW";

export function initDesktopI18n(lang: DesktopLang) {
  i18n.use(initReactI18next).init({
    resources: {
      en: { translation: en },
      "zh-TW": { translation: zhTW },
    },
    lng: lang,
    fallbackLng: "en",
    interpolation: { escapeValue: false },
  });
  return i18n;
}

export async function setDesktopLang(lang: DesktopLang) {
  await i18n.changeLanguage(lang);
  const { load } = await import("@tauri-apps/plugin-store");
  const store = await load("session.json");
  await store.set("lang", lang);
}

export default i18n;
