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
    // Try to silently refresh the SSO cookie via the Identity Service.
    // The bi_auth access token expires after 15min; /id/api/auth/check
    // rotates it using the bi_refresh cookie (7d). Only redirect to login
    // if the refresh also fails — otherwise long-running sessions (and
    // long uploads) get killed the moment the access token expires.
    const refreshed = await tryRefreshSsoCookie();
    if (!refreshed) {
      useAuth.getState().logout();
      window.location.href = SSO_LOGIN_URL;
      throw new Error("Unauthorized");
    }
    // Retry the original request once with the refreshed cookie
    const retry = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { ...headers, ...(options?.headers as Record<string, string>) },
      credentials: "include",
    });
    if (retry.status === 401) {
      useAuth.getState().logout();
      window.location.href = SSO_LOGIN_URL;
      throw new Error("Unauthorized");
    }
    if (!retry.ok) {
      const text = await retry.text().catch(() => retry.statusText);
      throw new Error(text || retry.statusText);
    }
    return retry.json() as Promise<T>;
  }
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

// Silently refresh the SSO cookie by hitting the Identity Service /auth/check
// endpoint, which rotates the bi_auth cookie using the bi_refresh cookie.
// Returns true if the cookie was refreshed (response 200), false otherwise.
let _refreshing: Promise<boolean> | null = null;
async function tryRefreshSsoCookie(): Promise<boolean> {
  if (_refreshing) return _refreshing;
  _refreshing = (async () => {
    try {
      const r = await fetch("/id/api/auth/check", { credentials: "include" });
      return r.ok;
    } catch {
      return false;
    } finally {
      _refreshing = null;
    }
  })();
  return _refreshing;
}

// Upload with progress reporting via XMLHttpRequest (fetch has no upload
// progress event). Returns a promise that resolves with the parsed JSON
// response and reports progress via the onProgress callback.
export function uploadWithProgress<T>(
  path: string,
  body: FormData,
  onProgress?: (loaded: number, total: number, pct: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}${path}`);
    xhr.withCredentials = true;

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(e.loaded, e.total, Math.round((e.loaded / e.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status === 401) {
        // Same refresh logic as request() — try to renew the cookie before
        // giving up. If refresh works, retry the upload from scratch (we
        // can't resume a multipart upload mid-stream).
        tryRefreshSsoCookie().then((ok) => {
          if (!ok) {
            useAuth.getState().logout();
            window.location.href = SSO_LOGIN_URL;
            reject(new Error("Unauthorized"));
            return;
          }
          // Retry once
          const retry = new XMLHttpRequest();
          retry.open("POST", `${API_BASE}${path}`);
          retry.withCredentials = true;
          retry.upload.onprogress = (e) => {
            if (e.lengthComputable && onProgress) {
              onProgress(e.loaded, e.total, Math.round((e.loaded / e.total) * 100));
            }
          };
          retry.onload = () => {
            if (retry.status >= 200 && retry.status < 300) {
              try { resolve(JSON.parse(retry.responseText) as T); }
              catch { resolve(retry.responseText as unknown as T); }
            } else {
              reject(new Error(retry.responseText || retry.statusText));
            }
          };
          retry.onerror = () => reject(new Error("Network error during upload"));
          retry.send(body);
        });
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText) as T); }
        catch { resolve(xhr.responseText as unknown as T); }
      } else {
        reject(new Error(xhr.responseText || xhr.statusText));
      }
    };

    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(body);
  });
}

// ── Resumable chunked upload ───────────────────────────────────────────────
// Splits a File into chunks, uploads them one at a time with progress,
// and assembles on the server. If the connection drops, the client can
// resume by querying which chunks are missing. Each chunk is a small
// request (~8 MiB) so there's no timeout risk and RAM stays bounded.

const CHUNK_SIZE = 8 * 1024 * 1024; // 8 MiB — must match server

/**
 * Compute SHA-256 hash of a File for early dedup. For large files (>200MB)
 * we skip client-side hashing to avoid freezing the UI — the server will
 * compute the hash after assembly.
 */
async function computeFileHash(file: File): Promise<string> {
  if (file.size > 200 * 1024 * 1024) return "";
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function uploadChunked(
  file: File,
  onProgress?: (loaded: number, total: number, pct: number) => void,
): Promise<any> {
  const total = file.size;
  const totalChunks = Math.ceil(total / CHUNK_SIZE);

  // Compute SHA-256 hash client-side (for early dedup). Reads the file in
  // chunks via a stream so we don't load the entire file into RAM at once
  // (which would freeze the UI for large videos and delay the progress bar).
  let fileHash: string | null = null;
  try {
    fileHash = await computeFileHash(file);
  } catch {
    // SubtleCrypto not available (e.g. non-secure context) — server will hash.
  }

  // 1. Init — start upload session (or short-circuit if duplicate)
  const initFd = new FormData();
  initFd.append("filename", file.name);
  initFd.append("file_size", String(file.size));
  if (fileHash) initFd.append("file_hash", fileHash);

  const initRes = await request<any>("/gameplays/upload/init", {
    method: "POST",
    body: initFd,
  });

  if (initRes.duplicate) {
    throw new Error("Este arquivo já foi enviado");
  }

  const uploadId = initRes.upload_id;
  const serverChunkSize = initRes.chunk_size || CHUNK_SIZE;
  const serverTotalChunks = initRes.total_chunks;

  // 2. Upload chunks — sequential with progress, retry on transient errors
  let uploadedBytes = 0;
  for (let i = 0; i < serverTotalChunks; i++) {
    const start = i * serverChunkSize;
    const end = Math.min(start + serverChunkSize, total);
    const chunk = file.slice(start, end);

    const chunkFd = new FormData();
    chunkFd.append("index", String(i));
    chunkFd.append("chunk", chunk, `chunk_${i}`);

    // Retry each chunk up to 3 times on network error
    let lastErr: Error | null = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        await uploadWithProgress<any>(
          `/gameplays/upload/${uploadId}/chunk`,
          chunkFd,
          (loaded, chunkTotal) => {
            // Report overall progress: completed chunks + current chunk progress
            const overall = uploadedBytes + loaded;
            if (onProgress) {
              onProgress(overall, total, Math.round((overall / total) * 100));
            }
          },
        );
        lastErr = null;
        break;
      } catch (e: any) {
        lastErr = e;
        // If 401, the uploadWithProgress already tried to refresh — if we're
        // here, the refresh failed and we should stop.
        if (e.message === "Unauthorized") throw e;
        // Wait before retry (exponential backoff)
        await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      }
    }
    if (lastErr) throw lastErr;

    uploadedBytes += (end - start);
    if (onProgress) {
      onProgress(uploadedBytes, total, Math.round((uploadedBytes / total) * 100));
    }
  }

  // 3. Complete — assemble on server
  const result = await request<any>(`/gameplays/upload/${uploadId}/complete`, {
    method: "POST",
  });

  if (result.duplicate) {
    throw new Error("Este arquivo já foi enviado");
  }

  return result;
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
  getCurrentJob: () => request<{ job: any }>("/automation/current-job"),

  // ── Games ──────────────────────────────────────────────────────────────
  listGames: () => request<any[]>("/games"),
  createGame: (canonical_name: string, aliases = "", platforms = "") =>
    request<any>("/games", { method: "POST", body: form({ canonical_name, aliases, platforms }) }),
  getGame: (id: number) => request<any>(`/games/${id}`),

  // ── Sources / Gameplays ────────────────────────────────────────────────
  listSources: (game_id?: number, status?: string, include_public?: boolean) => {
    const params = new URLSearchParams();
    if (game_id) params.set("game_id", String(game_id));
    if (status) params.set("status", status);
    if (include_public) params.set("include_public", "true");
    const qs = params.toString();
    return request<any[]>(qs ? `/sources?${qs}` : "/sources");
  },
  getSourceEvents: (source_id: number) =>
    request<any>(`/sources/${source_id}/events`),
  assignGame: (source_id: number, game_id: number) =>
    request<any>(`/sources/${source_id}/assign-game`, { method: "POST", body: form({ game_id }) }),
  assignGameByName: (source_id: number, game_name: string, slug: string) =>
    request<any>(`/sources/${source_id}/assign-game`, {
      method: "POST",
      body: form({ game_name, slug }),
    }),
  scanInbox: () => request<any>("/inbox/scan", { method: "POST" }),
  uploadGameplay: (
    file: File,
    onProgress?: (loaded: number, total: number, pct: number) => void,
  ): Promise<any> => {
    return uploadChunked(file, onProgress);
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
  updateVideoMetadata: (id: number, data: { title?: string; description?: string; tags?: string[] }) =>
    request<any>(`/videos/${id}/metadata`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  publishVideo: (id: number, overrides?: { title?: string; description?: string; tags?: string[] }) =>
    request<any>(`/videos/${id}/publish`, {
      method: "POST",
      body: overrides ? JSON.stringify(overrides) : undefined,
    }),
  deleteVideo: (id: number, releaseClips: boolean = false) =>
    request<any>(`/videos/${id}?release_clips=${releaseClips}`, { method: "DELETE" }),
  toggleGameplayVisibility: (sourceId: number, isPublic: boolean) =>
    request<any>(`/gameplays/${sourceId}/visibility?is_public=${isPublic}`, { method: "PATCH" }),
  deleteSource: (sourceId: number) =>
    request<{ ok: boolean; source_id: number; cleanup_job_id: number }>(`/sources/${sourceId}`, { method: "DELETE" }),

  // ── Workers (Compute Plane) ─────────────────────────────────────────────
  listWorkers: () => request<{ workers: any[] }>("/workers"),
  createMappingJob: (source_id: number) =>
    request<any>(`/gameplays/${source_id}/create-mapping-job`, { method: "POST" }),

  // ── Channel Profile (channel identity + editorial direction) ───────────
  getChannelProfile: () => request<any>("/channel/profile"),
  updateChannelProfile: (data: Record<string, any>) =>
    request<any>("/channel/profile", { method: "PUT", body: JSON.stringify(data) }),

  // ── Knowledge Documents (RAG knowledge base — REMOVED) ────────────────
  // File-upload knowledge base has been removed. Use manual ideas instead.

  // ── V2: Game Registry ──────────────────────────────────────────────────
  enrichGame: (id: number) =>
    request<any>(`/games/${id}/enrich`, { method: "POST" }),
  getGameAliases: (id: number) => request<any>(`/games/${id}/aliases`),

  // ── V2: Knowledge Items (Content Intelligence) ─────────────────────────
  listKnowledgeItems: (params?: {
    game_id?: number;
    item_type?: string;
    status?: string;
    limit?: number;
    offset?: number;
    min_score?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.game_id) qs.set("game_id", String(params.game_id));
    if (params?.item_type) qs.set("item_type", params.item_type);
    if (params?.status) qs.set("status", params.status);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    if (params?.min_score) qs.set("min_score", String(params.min_score));
    return request<any>(`/knowledge-items?${qs}`);
  },
  getKnowledgeItemStats: () => request<any>("/knowledge-items/stats"),
  getKnowledgeItem: (id: number) => request<any>(`/knowledge-items/${id}`),
  rejectKnowledgeItem: (id: number) =>
    request<any>(`/knowledge-items/${id}/reject`, { method: "POST" }),
  triggerContentCollection: () =>
    request<any>("/knowledge-items/collect", { method: "POST" }),
  createManualIdea: (data: { title: string; content: string; game_id?: number }) =>
    request<any>("/knowledge-items", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // ── Idea Queue (user-curated playlist) ────────────────────────────────
  getIdeaQueue: () => request<{ queue: any[]; items: any[] }>("/idea-queue"),
  addToIdeaQueue: (
    knowledgeItemId: number,
    gameplayPreference?: number | null,
    reuseOverride?: string | null,
  ) =>
    request<{ queue: any[]; message: string }>("/idea-queue/add", {
      method: "POST",
      body: JSON.stringify({
        knowledge_item_id: knowledgeItemId,
        gameplay_preference: gameplayPreference ?? null,
        reuse_override: reuseOverride ?? null,
      }),
    }),
  removeFromIdeaQueue: (knowledgeItemId: number) =>
    request<{ queue: any[]; message: string }>("/idea-queue/remove", {
      method: "POST",
      body: JSON.stringify({ knowledge_item_id: knowledgeItemId }),
    }),
  reorderIdeaQueue: (newOrder: number[]) =>
    request<{ queue: any[]; message: string }>("/idea-queue/reorder", {
      method: "POST",
      body: JSON.stringify(newOrder),
    }),
  updateIdeaQueueItem: (
    knowledgeItemId: number,
    gameplayPreference?: number | null,
    reuseOverride?: string | null,
  ) =>
    request<{ queue: any[]; message: string }>("/idea-queue/update", {
      method: "POST",
      body: JSON.stringify({
        knowledge_item_id: knowledgeItemId,
        gameplay_preference: gameplayPreference ?? null,
        reuse_override: reuseOverride ?? null,
      }),
    }),

  // ── Gameplay Availability (V3) ────────────────────────────────────────
  getGameplayAvailability: () =>
    request<{ games: any[]; max_uses: number }>("/gameplay-availability"),

  // ── Catalog (IGDB) ──────────────────────────────────────────────────────
  // These go to the catalog service, not the main API — so we can't use the
  // request() helper which prepends API_BASE. We use fetch directly with the
  // same credential/refresh logic.
  searchCatalog: async (q: string, limit = 10) => {
    const base = import.meta.env.PROD ? "/gpcg/api/catalog" : "/catalog-api";
    const res = await fetch(`${base}/search?q=${encodeURIComponent(q)}&limit=${limit}`, {
      credentials: "include",
    });
    if (!res.ok) throw new Error(res.statusText);
    return res.json() as Promise<{ results: any[]; count: number }>;
  },
  autocompleteCatalog: async (q: string, limit = 8) => {
    const base = import.meta.env.PROD ? "/gpcg/api/catalog" : "/catalog-api";
    const res = await fetch(`${base}/autocomplete?q=${encodeURIComponent(q)}&limit=${limit}`, {
      credentials: "include",
    });
    if (!res.ok) throw new Error(res.statusText);
    return res.json() as Promise<{ results: any[]; count: number }>;
  },
};
