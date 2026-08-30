/**
 * i18n initialization for the React Native mobile app.
 *
 * Bundles JSON translation files directly (no HTTP backend needed).
 * Language detection via react-native-localize.
 *
 * Fallback language: pt-BR (the primary language of GPCG).
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import * as RNLocalize from "react-native-localize";

export const SUPPORTED_LANGUAGES = ["pt-BR", "en"] as const;
export const FALLBACK_LANGUAGE = "pt-BR";
export const NAMESPACES = [
  "common",
  "jobs",
  "stages",
  "errors",
  "dashboard",
  "automation",
] as const;

export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];
export type Namespace = (typeof NAMESPACES)[number];

// Bundled locale resources (populated by scripts/sync-i18n.js)
import ptBR_common from "./locales/pt-BR/common.json";
import ptBR_jobs from "./locales/pt-BR/jobs.json";
import ptBR_stages from "./locales/pt-BR/stages.json";
import ptBR_errors from "./locales/pt-BR/errors.json";
import ptBR_dashboard from "./locales/pt-BR/dashboard.json";
import ptBR_automation from "./locales/pt-BR/automation.json";

import en_common from "./locales/en/common.json";
import en_jobs from "./locales/en/jobs.json";
import en_stages from "./locales/en/stages.json";
import en_errors from "./locales/en/errors.json";
import en_dashboard from "./locales/en/dashboard.json";
import en_automation from "./locales/en/automation.json";

const resources = {
  "pt-BR": {
    common: ptBR_common,
    jobs: ptBR_jobs,
    stages: ptBR_stages,
    errors: ptBR_errors,
    dashboard: ptBR_dashboard,
    automation: ptBR_automation,
  },
  en: {
    common: en_common,
    jobs: en_jobs,
    stages: en_stages,
    errors: en_errors,
    dashboard: en_dashboard,
    automation: en_automation,
  },
} as const;

/** Determine the best language from device locale, falling back to pt-BR. */
function getDeviceLanguage(): string {
  const bestMatch = RNLocalize.findBestLanguageTag([...SUPPORTED_LANGUAGES]);
  return bestMatch?.languageTag || FALLBACK_LANGUAGE;
}

void i18n.use(initReactI18next).init({
  lng: getDeviceLanguage(),
  fallbackLng: FALLBACK_LANGUAGE,
  supportedLngs: [...SUPPORTED_LANGUAGES],
  ns: [...NAMESPACES],
  defaultNS: "common",
  resources,
  interpolation: {
    escapeValue: false, // React already escapes by default
  },
  react: {
    useSuspense: false,
  },
});

export default i18n;
