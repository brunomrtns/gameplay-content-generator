import { useAuth } from "./auth";

// In production, the app is served under /gpcg/ and API calls go to /gpcg/api/
// In dev, the app is at / and API calls go to /api/ (proxied by Vite)
const API_BASE = import.meta.env.PROD ? "/gpcg/api" : "/api";

// SSO redirect target for BI Identity login
const SSO_LOGIN_URL = "/id/login?redirect=/gpcg/dashboard";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {};
  if (options?.body instanceof FormData) {
    // Let browser set Content-Type for FormData
  } else if (options?.body) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { ...headers, ...(options?.headers as Record<string, string>) },
    credentials: "include", // CRITICAL: send bi_auth cookie
  });
  if (res.status === 401) {
    useAuth.getState().logout();
    window.location.href = SSO_LOGIN_URL;
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export function form(data: Record<string, string | number | boolean>): FormData {
  const fd = new FormData();
  for (const [k, v] of Object.entries(data)) {
    if (v === "" || v === 0 || v === undefined || v === null) continue;
    if (typeof v === "boolean" && !v) continue; // skip false booleans for form fields
    fd.append(k, String(v));
  }
  return fd;
}

export function jsonBody(data: Record<string, any>): string {
  return JSON.stringify(data);
}

export const api = {
  // ── Auth (BI Identity SSO) ──────────────────────────────────────────────
  getMe: () => request<any>("/auth/me"),
  ssoRedirect: () => `${API_BASE}/auth/sso-redirect`,
  logout: () => request<{ redirect: string }>("/auth/logout", { method: "POST" }),
  listUsers: () => request<any[]>("/auth/users"),
  deleteUser: (id: number) => request<any>(`/auth/users/${id}`, { method: "DELETE" }),
  updateUser: (id: number, data: { name?: string; is_active?: boolean }) =>
    request<any>(`/auth/users/${id}`, { method: "PUT", body: JSON.stringify(data) }),

  // ── Automation ─────────────────────────────────────────────────────────
  getAutomation: () => request<any>("/automation"),
  updateAutomation: (data: { name?: string; config?: any; upload_config?: any; schedule?: string }) =>
    request<any>("/automation", { method: "PUT", body: JSON.stringify(data) }),
  startAutomation: () => request<{ status: string }>("/automation/start", { method: "POST" }),
  pauseAutomation: () => request<{ status: string }>("/automation/pause", { method: "POST" }),

  // ── YouTube ────────────────────────────────────────────────────────────
  youtubeConnect: () => request<{ url: string }>("/youtube/connect"),
  youtubeStatus: () =>
    request<{ connected: boolean; channel_title: string | null }>("/youtube/status"),
  youtubeDisconnect: () => request<any>("/youtube/disconnect", { method: "POST" }),

  // ── Dashboard ───────────────────────────────────────────────────────────
  getDashboard: () => request<any>("/dashboard"),

  // ── Games ──────────────────────────────────────────────────────────────
  listGames: () => request<any[]>("/games"),
  createGame: (canonical_name: string, aliases = "", platforms = "") =>
    request<any>("/games", { method: "POST", body: form({ canonical_name, aliases, platforms }) }),
  getGame: (id: number) => request<any>(`/games/${id}`),

  // ── Sources / Gameplays ────────────────────────────────────────────────
  listSources: (game_id?: number, status?: string) =>
    request<any[]>(
      `/sources${game_id ? `?game_id=${game_id}` : ""}${status ? `${game_id ? "&" : "?"}status=${status}` : ""}`
    ),
  assignGame: (source_id: number, game_id: number) =>
    request<any>(`/sources/${source_id}/assign-game`, { method: "POST", body: form({ game_id }) }),
  scanInbox: () => request<any>("/inbox/scan", { method: "POST" }),
  uploadGameplay: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<any>("/gameplays/upload", { method: "POST", body: fd });
  },

  // ── Assets ─────────────────────────────────────────────────────────────
  listAssets: (game_id: number) => request<any[]>(`/assets?game_id=${game_id}`),
  createAsset: (source_id: number, start_sec: number, end_sec: number, label = "") =>
    request<any>("/assets", { method: "POST", body: form({ source_id, start_sec, end_sec, label }) }),
  deleteAsset: (id: number) => request<any>(`/assets/${id}`, { method: "DELETE" }),

  // ── Documents ──────────────────────────────────────────────────────────
  listDocuments: (game_id?: number, general?: boolean) =>
    request<any[]>(
      `/documents${game_id ? `?game_id=${game_id}` : general ? `?general=true` : ""}`
    ),
  uploadDocument: (game_id: number | null, file: File) => {
    const fd = new FormData();
    if (game_id !== null) fd.append("game_id", String(game_id));
    fd.append("file", file);
    return request<any>("/documents/upload", { method: "POST", body: fd });
  },
  extractFacts: (doc_id: number) =>
    request<any>(`/documents/${doc_id}/extract-facts`, { method: "POST" }),

  // ── Facts ──────────────────────────────────────────────────────────────
  listFacts: (game_id?: number, general?: boolean) =>
    request<any[]>(`/facts${game_id ? `?game_id=${game_id}` : general ? `?general=true` : ""}`),

  // ── Content plans + scripts ────────────────────────────────────────────
  listPlans: (game_id?: number) => request<any[]>(`/content-plans${game_id ? `?game_id=${game_id}` : ""}`),
  getScript: (id: number) => request<any>(`/scripts/${id}`),

  // ── Jobs ───────────────────────────────────────────────────────────────
  listJobs: (status?: string) => request<any[]>(`/jobs${status ? `?status=${status}` : ""}`),
  createJob: (game_id: number, opts?: Record<string, any>) =>
    request<any>("/jobs/generate", { method: "POST", body: form({ game_id, ...(opts || {}) }) }),
  createCuriosityJob: (background_game_id: number, fact_id?: number, opts?: Record<string, any>) =>
    request<any>("/jobs/curiosity", {
      method: "POST",
      body: form({ background_game_id, ...(fact_id ? { fact_id } : {}), ...(opts || {}) }),
    }),

  // ── Voices (TTS) ───────────────────────────────────────────────────────
  listVoices: () => request<any[]>("/voices"),
  uploadVoice: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<any>("/voices/upload", { method: "POST", body: fd });
  },
  deleteVoice: (filename: string) =>
    request<any>(`/voices/${encodeURIComponent(filename)}`, { method: "DELETE" }),

  // ── Videos ─────────────────────────────────────────────────────────────
  listVideos: (game_id?: number) =>
    request<any[]>(`/videos${game_id ? `?game_id=${game_id}` : ""}`),
  videoUrl: (id: number) => `${API_BASE}/videos/${id}/file`,
  thumbUrl: (id: number) => `${API_BASE}/videos/${id}/thumbnail`,

  // ── Workers (Compute Plane) ─────────────────────────────────────────────
  listWorkers: () => request<{ workers: any[] }>("/workers"),
  createMappingJob: (source_id: number) =>
    request<any>(`/gameplays/${source_id}/create-mapping-job`, { method: "POST" }),
};
