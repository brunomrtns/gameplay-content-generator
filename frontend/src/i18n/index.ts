/**
 * i18n initialization for the web frontend.
 *
 * Uses i18next-http-backend to load JSON translation files from
 * /locales/<lang>/<namespace>.json (served statically from public/locales).
 * Language detection via i18next-browser-languagedetector.
 *
 * Fallback language: pt-BR (the primary language of GPCG).
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import HttpBackend from "i18next-http-backend";
import LanguageDetector from "i18next-browser-languagedetector";

export const SUPPORTED_LANGUAGES = ["pt-BR", "en"] as const;
export const FALLBACK_LANGUAGE = "pt-BR";
export const NAMESPACES = [
  "common",
  "jobs",
  "stages",
  "errors",
  "dashboard",
  "automation",
  "admin",
  "kids",
  "content",
  "videos",
  "login",
  "landing",
] as const;

export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];
export type Namespace = (typeof NAMESPACES)[number];

void i18n
  .use(HttpBackend)
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng: FALLBACK_LANGUAGE,
    supportedLngs: [...SUPPORTED_LANGUAGES],
    ns: [...NAMESPACES],
    defaultNS: "common",
    backend: {
      // In production the app is served under /gpcg/, so the loadPath
      // must include the base path. Vite sets import.meta.env.BASE_URL.
      loadPath: `${import.meta.env.BASE_URL}locales/{{lng}}/{{ns}}.json`,
    },
    detection: {
      order: ["localStorage", "navigator", "htmlTag"],
      caches: ["localStorage"],
      lookupLocalStorage: "gpcg_lang",
    },
    interpolation: {
      escapeValue: false, // React already escapes by default
    },
    react: {
      useSuspense: false,
    },
  });

export default i18n;
