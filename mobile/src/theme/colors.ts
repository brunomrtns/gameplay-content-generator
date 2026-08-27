// GPCG Mobile Theme — matches the web dark theme
// Web: bg #07070a, accent teal hsl(172,72%,44%)

export const colors = {
  // Backgrounds
  bg: '#07070a',
  surface: '#0f0f14',
  surfaceElevated: '#16161e',
  surfaceHover: '#1c1c26',

  // Borders
  border: '#22222e',
  borderBright: '#33333f',

  // Text
  text: '#f4f4f5',
  textSecondary: '#a1a1aa',
  textMuted: '#71717a',

  // Accent (teal)
  accent: '#2dd4bf',
  accentHover: '#14b8a6',
  accentWarm: '#f59e0b',

  // Status
  success: '#22c55e',
  warning: '#eab308',
  error: '#ef4444',
  info: '#3b82f6',

  // YouTube
  youtube: '#ff0000',
} as const;

export type ColorName = keyof typeof colors;
