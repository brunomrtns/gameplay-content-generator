/**
 * Domain Configuration System
 *
 * Each domain (games, kids, movies, ...) has a DomainConfig that defines:
 * - identity: name, description, icon
 * - theme: design tokens (colors, fonts, radius)
 * - features: which features are enabled
 * - navigation: sidebar/nav items
 * - content: labels and terminology
 *
 * The domain is fetched from the backend (/api/channel/domains) and
 * applied via the DomainProvider context. Components read domain config
 * from useDomain() instead of hardcoding if/else chains.
 */

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api } from "./api";

// ── Types ────────────────────────────────────────────────────────────────────

export interface DomainTheme {
  /** Primary accent color (HSL string for CSS variable) */
  accent: string;
  accentHover: string;
  accentGlow: string;
  /** Warm accent for badges/highlights */
  accentWarm: string;
  /** Background colors */
  bg: string;
  bgDeep: string;
  surface: string;
  surfaceElevated: string;
  surfaceHover: string;
  /** Border colors */
  border: string;
  borderBright: string;
  /** Text colors */
  text: string;
  textSecondary: string;
  textMuted: string;
  /** Font family override (optional) */
  fontFamily?: string;
  /** Border radius for cards/buttons */
  radius: string;
  /** Logo emoji or icon name */
  logoIcon: string;
  /** App name shown in header */
  appName: string;
}

export interface DomainNavitem {
  to: string;
  label: string;
  icon: string; // lucide icon name
}

export interface DomainFeatures {
  gameplayUpload: boolean;
  ideas: boolean;
  topics: boolean;
  gameRegistry: boolean;
  knowledgeItems: boolean;
  curiosityShorts: boolean;
}

export interface DomainContent {
  /** Terminology for this domain */
  sourceLabel: string;     // "gameplay" or "imagem"
  sourceLabelPlural: string;
  assetLabel: string;      // "trecho" or "imagem"
  assetLabelPlural: string;
  createLabel: string;     // "Enviar gameplays" or "Criar tópicos"
  contentTitle: string;    // "Conteúdo" or "Tópicos"
}

export interface DomainConfig {
  id: string;
  name: string;
  description: string;
  implemented: boolean;
  theme: DomainTheme;
  features: DomainFeatures;
  navigation: DomainNavitem[];
  content: DomainContent;
}

// ── Theme Definitions ────────────────────────────────────────────────────────

const GAMES_THEME: DomainTheme = {
  accent: "hsl(172, 72%, 44%)",
  accentHover: "hsl(172, 72%, 52%)",
  accentGlow: "hsla(172, 72%, 44%, 0.2)",
  accentWarm: "hsl(38, 88%, 60%)",
  bg: "#07070a",
  bgDeep: "#050507",
  surface: "#0d0d11",
  surfaceElevated: "#14141a",
  surfaceHover: "#1c1c24",
  border: "#1e1e28",
  borderBright: "#2e2e3a",
  text: "#f5f5f7",
  textSecondary: "#a0a0aa",
  textMuted: "#5a5a66",
  fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
  radius: "0.75rem",
  logoIcon: "Zap",
  appName: "GPCG",
};

const KIDS_THEME: DomainTheme = {
  accent: "hsl(280, 80%, 60%)",
  accentHover: "hsl(280, 80%, 68%)",
  accentGlow: "hsla(280, 80%, 60%, 0.25)",
  accentWarm: "hsl(45, 90%, 60%)",
  bg: "#1a0d2e",
  bgDeep: "#120822",
  surface: "#251539",
  surfaceElevated: "#2e1a47",
  surfaceHover: "#382155",
  border: "#3d2659",
  borderBright: "#5c3d80",
  text: "#fdf2ff",
  textSecondary: "#c4a8d4",
  textMuted: "#8a7a9e",
  fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
  radius: "1rem",
  logoIcon: "Baby",
  appName: "GPCG Kids",
};

const MOVIES_THEME: DomainTheme = {
  accent: "hsl(0, 70%, 55%)",
  accentHover: "hsl(0, 70%, 63%)",
  accentGlow: "hsla(0, 70%, 55%, 0.2)",
  accentWarm: "hsl(45, 85%, 55%)",
  bg: "#0a0a0d",
  bgDeep: "#060608",
  surface: "#12121a",
  surfaceElevated: "#1a1a26",
  surfaceHover: "#242434",
  border: "#222230",
  borderBright: "#363648",
  text: "#f5f5f7",
  textSecondary: "#a0a0b0",
  textMuted: "#5a5a6e",
  fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
  radius: "0.5rem",
  logoIcon: "Film",
  appName: "GPCG Movies",
};

// ── Domain Configs ───────────────────────────────────────────────────────────

export const DOMAIN_CONFIGS: Record<string, DomainConfig> = {
  games: {
    id: "games",
    name: "Games",
    description: "Geração de vídeos de gameplay com IA",
    implemented: true,
    theme: GAMES_THEME,
    features: {
      gameplayUpload: true,
      ideas: true,
      topics: false,
      gameRegistry: true,
      knowledgeItems: true,
      curiosityShorts: true,
    },
    navigation: [
      { to: "/dashboard", label: "Dashboard", icon: "LayoutDashboard" },
      { to: "/content", label: "Conteúdo", icon: "FileText" },
      { to: "/ideas", label: "Ideias", icon: "Lightbulb" },
      { to: "/jobs", label: "Jobs", icon: "ListChecks" },
      { to: "/automation", label: "Automação", icon: "Settings" },
      { to: "/videos", label: "Vídeos", icon: "Video" },
    ],
    content: {
      sourceLabel: "gameplay",
      sourceLabelPlural: "gameplays",
      assetLabel: "trecho",
      assetLabelPlural: "trechos",
      createLabel: "Enviar gameplays",
      contentTitle: "Conteúdo",
    },
  },

  kids: {
    id: "kids",
    name: "Kids",
    description: "Geração de vídeos infantis educativos com IA",
    implemented: true,
    theme: KIDS_THEME,
    features: {
      gameplayUpload: false,
      ideas: false,
      topics: true,
      gameRegistry: false,
      knowledgeItems: false,
      curiosityShorts: false,
    },
    navigation: [
      { to: "/dashboard", label: "Dashboard", icon: "LayoutDashboard" },
      { to: "/kids", label: "Tópicos", icon: "Baby" },
      { to: "/jobs", label: "Jobs", icon: "ListChecks" },
      { to: "/automation", label: "Automação", icon: "Settings" },
      { to: "/videos", label: "Vídeos", icon: "Video" },
    ],
    content: {
      sourceLabel: "imagem",
      sourceLabelPlural: "imagens",
      assetLabel: "imagem",
      assetLabelPlural: "imagens",
      createLabel: "Criar tópicos",
      contentTitle: "Tópicos",
    },
  },

  movies: {
    id: "movies",
    name: "Movies",
    description: "Geração de vídeos sobre cinema com IA",
    implemented: false,
    theme: MOVIES_THEME,
    features: {
      gameplayUpload: false,
      ideas: false,
      topics: false,
      gameRegistry: false,
      knowledgeItems: false,
      curiosityShorts: false,
    },
    navigation: [
      { to: "/dashboard", label: "Dashboard", icon: "LayoutDashboard" },
      { to: "/jobs", label: "Jobs", icon: "ListChecks" },
      { to: "/automation", label: "Automação", icon: "Settings" },
      { to: "/videos", label: "Vídeos", icon: "Video" },
    ],
    content: {
      sourceLabel: "clip",
      sourceLabelPlural: "clips",
      assetLabel: "scene",
      assetLabelPlural: "scenes",
      createLabel: "Adicionar clipes",
      contentTitle: "Conteúdo",
    },
  },
};

// ── Domain Context ───────────────────────────────────────────────────────────

interface DomainContextValue {
  domain: string;
  config: DomainConfig;
  setDomain: (domain: string) => void;
}

const DEFAULT_DOMAIN = "games";

const DomainContext = createContext<DomainContextValue>({
  domain: DEFAULT_DOMAIN,
  config: DOMAIN_CONFIGS[DEFAULT_DOMAIN],
  setDomain: () => {},
});

export function DomainProvider({ children }: { children: ReactNode }) {
  const [domain, setDomainState] = useState<string>(DEFAULT_DOMAIN);

  useEffect(() => {
    // Fetch domain from backend on mount
    api.getDashboard()
      .then((d) => {
        const domain = d.channel_domain || DEFAULT_DOMAIN;
        setDomainState(domain);
        applyTheme(domain);
      })
      .catch(() => {
        // If dashboard fails (e.g. not yet authenticated), use default
      });
  }, []);

  const setDomain = (newDomain: string) => {
    setDomainState(newDomain);
    applyTheme(newDomain);
  };

  const config = DOMAIN_CONFIGS[domain] || DOMAIN_CONFIGS[DEFAULT_DOMAIN];

  return (
    <DomainContext.Provider value={{ domain, config, setDomain }}>
      {children}
    </DomainContext.Provider>
  );
}

export function useDomain() {
  return useContext(DomainContext);
}

// ── Theme Application ────────────────────────────────────────────────────────

/** Apply domain theme by setting CSS variables on :root */
export function applyTheme(domain: string) {
  const config = DOMAIN_CONFIGS[domain] || DOMAIN_CONFIGS[DEFAULT_DOMAIN];
  const theme = config.theme;
  const root = document.documentElement;

  root.style.setProperty("--color-accent", theme.accent);
  root.style.setProperty("--color-accent-hover", theme.accentHover);
  root.style.setProperty("--color-accent-glow", theme.accentGlow);
  root.style.setProperty("--color-accent-warm", theme.accentWarm);
  root.style.setProperty("--color-bg", theme.bg);
  root.style.setProperty("--color-bg-deep", theme.bgDeep);
  root.style.setProperty("--color-surface", theme.surface);
  root.style.setProperty("--color-surface-elevated", theme.surfaceElevated);
  root.style.setProperty("--color-surface-hover", theme.surfaceHover);
  root.style.setProperty("--color-border", theme.border);
  root.style.setProperty("--color-border-bright", theme.borderBright);
  root.style.setProperty("--color-text", theme.text);
  root.style.setProperty("--color-text-secondary", theme.textSecondary);
  root.style.setProperty("--color-text-muted", theme.textMuted);

  if (theme.fontFamily) {
    root.style.setProperty("--font-family", theme.fontFamily);
  }
  root.style.setProperty("--radius-default", theme.radius);
}
