import { client, saveToken, getSSOCookies, clearAuth, videoUrl, thumbUrl, presentationImageUrl } from './client';

// ── Types ────────────────────────────────────────────────────────────────────

export interface User {
  id: number;
  email: string;
  name: string;
  is_active: boolean;
  is_admin: boolean;
  google_user_id?: string | null;
  has_youtube: boolean;
  channel_title?: string;
  channel_domain?: string;
  onboarding_completed?: boolean;
  created_at?: string;
}

export interface QueueEntry {
  ki_id: number;
  gameplay_preference: number | null;
  reuse_override: string | null;
  gameplay_source_id: number | null;
}

export interface KnowledgeItem {
  id: number;
  title: string;
  content: string;
  item_type: string;
  source_type: string;
  status: string;
  score: number;
  editorial_score: number;
  franchise: string | null;
  developer: string | null;
  tags: string[];
  game_id?: number | null;
  game_name?: string | null;
  gameplay_preference?: number | null;
  reuse_override?: string | null;
  gameplay_source_id?: number | null;
  gameplay_source_filename?: string | null;
}

export interface GameAvailability {
  game_id: number;
  game_name: string;
  ownership: 'own' | 'public';
  availability: 'abundant' | 'partial' | 'low' | 'none' | 'reuse_only';
  total_sources: number;
  available_seconds: number;
  used_seconds: number;
  eligible_events: number;
  total_events: number;
}

export interface GameplaySourceInfo {
  source_id: number;
  filename: string | null;
  free_seconds: number;
  total_seconds: number;
  eligible_events: number;
  total_events: number;
  availability: string;
}

export interface CatalogGame {
  id: number;
  name: string;
  release_year?: number | null;
  cover_url?: string | null;
  slug?: string;
}

export interface Game {
  id: number;
  canonical_name: string;
  slug: string;
  description?: string | null;
  developer?: string | null;
  publisher?: string | null;
  franchise?: string | null;
  genres: string[];
  themes: string[];
  lore_summary?: string | null;
  release_date?: string | null;
  camera_type: string;
  platforms: string[];
  capture_sources: string[];
  enriched_at?: string | null;
}

export interface GameplaySource {
  id: number;
  user_id?: number | null;
  game_id?: number | null;
  filename: string;
  file_hash?: string | null;
  file_size: number;
  duration: number;
  width: number;
  height: number;
  fps: number;
  codec?: string | null;
  has_audio: boolean;
  ingestion_status: string;
  processing_status?: string | null;
  is_public: boolean;
  enabled: boolean;
  storage_key?: string | null;
  capture_source?: string | null;
  game_name?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface GameplayEvent {
  id: number;
  source_id: number;
  start_time: number;
  end_time: number;
  event_type: string;
  description: string;
  interesting_score: number;
  visual_confidence: number;
  analysis_version?: string;
}

export interface Job {
  id: number;
  user_id?: number | null;
  job_uuid: string;
  type: string;
  domain: string;
  game_id?: number | null;
  content_plan_id?: number | null;
  gameplay_source_id?: number | null;
  status: string;
  stage: string;
  progress: number;
  attempts: number;
  max_attempts: number;
  error?: string | null;
  artifacts: Record<string, any>;
  worker_id?: number | null;
  priority: string;
  required_capabilities: string[];
  created_at: string;
  started_at?: string | null;
  updated_at: string;
  completed_at?: string | null;
  // Enriched fields from API (joined from ContentPlan/KnowledgeItem/Game)
  ki_title?: string;
  ki_item_type?: string;
  stage_label?: string;
  game_name?: string;
  topic?: string;
}

export interface Video {
  id: number;
  user_id?: number | null;
  job_id?: number | null;
  content_plan_id?: number | null;
  game_id?: number | null;
  file_path: string;
  storage_key?: string | null;
  duration: number;
  width: number;
  height: number;
  qa_score: number;
  qa_report: Record<string, any>;
  status: string;
  thumbnail_path?: string | null;
  youtube_url?: string | null;
  youtube_video_id?: string | null;
  knowledge_item_id?: number | null;
  created_at: string;
  // Enriched fields from API (joined from ContentPlan/KnowledgeItem)
  title?: string;
  social_title?: string;
  topic?: string;
  description?: string;
  tags?: string[];
  qa_passed?: boolean;
  game_name?: string;
}

export interface Worker {
  id: number;
  worker_id: string;
  hostname: string;
  status: string;
  last_heartbeat?: string | null;
  last_status_at?: string | null;
  current_job_id?: number | null;
  current_activity?: string | null;
  gpu_name?: string | null;
  gpu_usage?: number | null;
  cpu_usage?: number | null;
  ram_usage?: number | null;
  capabilities: string[];
  worker_version?: string | null;
  git_commit?: string | null;
  build_number?: string | null;
  metadata_json: Record<string, any>;
  registered_at: string;
  updated_at: string;
}

export interface Voice {
  filename: string;
  size: number;
  created_at?: string;
}

export interface Dashboard {
  gameplays: { total: number; processing: number; ready: number };
  jobs: { total: number; running: number };
  videos: { total: number; published: number };
  youtube_connected: boolean;
  automation_running: boolean;
  worker_status?: string | null;
  worker_activity?: string | null;
  recent_videos?: Video[];
  channel_domain?: string;
  [key: string]: any;
}

export interface AutomationConfig {
  enabled: boolean;
  video_format: string;
  voice: string;
  creative_style: string;
  max_clip_uses: number;
  fallback_policy: string;
  accept_public_gameplays: boolean;
  auto_fill_queue: boolean;
  max_queue_size: number;
  presentation?: Record<string, any>;
  [key: string]: any;
}

export interface YouTubeStatus {
  connected: boolean;
  channel_title?: string;
  channel_id?: string;
  thumbnail_url?: string;
}

export interface ChannelProfile {
  name: string;
  description: string;
  domain: string;
  language: string;
  // Multilingual: target_language controls script/TTS/subtitle language
  target_language: string;
  prompt_version: string;
  [key: string]: any;
}

export interface CollectionFocus {
  type: 'game' | 'topic' | 'game+topic' | null;
  game_id?: number | null;
  game_name?: string | null;
  topic?: string | null;
  item_types?: string[];
}

export interface KnowledgeItemStats {
  total: number;
  fresh: number;
  used: number;
  rejected: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  by_source: Record<string, number>;
}

export interface KidsTopic {
  id: number;
  name: string;
  description?: string;
  created_at?: string;
}

export interface KidsLibraryAsset {
  id: number;
  filename: string;
  media_kind: string;
  status: string;
  tags?: string;
  description?: string;
  topic_id?: number | null;
  topic_name?: string | null;
  is_public: boolean;
  duration?: number;
  storage_key?: string | null;
  created_at?: string;
}

export interface KidsIdea {
  id: number;
  title: string;
  content: string;
  category: string;
  status: string;
  score?: number;
  topic_id?: number | null;
  topic_name?: string | null;
  job_id?: number;
  description?: string;
  created_at?: string;
}

export interface KidsIdeaStats {
  total: number;
  fresh: number;
  used: number;
  rejected: number;
  by_category: Record<string, number>;
}

export interface TopicLibraryEntry {
  id: number;
  topic: string;
  category: string;
  description?: string;
}

export interface SeasonalCalendarEntry {
  month: string;
  events: Array<{ name: string; date: string; description?: string }>;
}

export interface DomainInfo {
  name: string;
  label: string;
  description: string;
}

export interface AppVersionInfo {
  available: boolean;
  version: string | null;
  versionCode: number | null;
  download_url: string | null;
  released_at: string | null;
  changelog: string | null;
  size_bytes: number | null;
}

// ── Auth ─────────────────────────────────────────────────────────────────────

export const authApi = {
  /** Exchange SSO cookies for JWT token (mobile auth flow) */
  async exchangeToken(): Promise<{ token: string; user: User }> {
    const cookies = await getSSOCookies();
    if (!cookies) throw new Error('No SSO cookies found');
    const { data } = await client.post<{ token: string; user: User }>('/auth/token', cookies);
    await saveToken(data.token, data.user);
    return data;
  },

  async getMe(): Promise<User> {
    const { data } = await client.get<User>('/auth/me');
    return data;
  },

  async logout(): Promise<void> {
    try {
      await client.post('/auth/logout');
    } catch {}
    await clearAuth();
  },

  async listUsers(): Promise<User[]> {
    const { data } = await client.get<User[]>('/auth/users');
    return data;
  },

  async updateUser(userId: number, payload: { name?: string; is_active?: boolean }): Promise<User> {
    const { data } = await client.put<User>(`/auth/users/${userId}`, payload);
    return data;
  },

  async deleteUser(userId: number): Promise<void> {
    await client.delete(`/auth/users/${userId}`);
  },

  // ── Onboarding ─────────────────────────────────────────────────────────
  async getOnboarding(): Promise<{ completed: boolean }> {
    const { data } = await client.get<{ completed: boolean }>('/auth/onboarding');
    return data;
  },

  async completeOnboarding(): Promise<{ completed: boolean }> {
    const { data } = await client.post<{ completed: boolean }>('/auth/onboarding/complete');
    return data;
  },

  async resetOnboarding(): Promise<{ completed: boolean }> {
    const { data } = await client.post<{ completed: boolean }>('/auth/onboarding/reset');
    return data;
  },
};

// ── Dashboard ────────────────────────────────────────────────────────────────

export const dashboardApi = {
  async get(): Promise<Dashboard> {
    const { data } = await client.get<Dashboard>('/dashboard');
    return data;
  },
};

// ── Automation ───────────────────────────────────────────────────────────────

export const automationApi = {
  async get(): Promise<AutomationConfig> {
    const { data } = await client.get<AutomationConfig>('/automation');
    return data;
  },

  async update(config: Partial<AutomationConfig>): Promise<void> {
    await client.patch('/automation', { config });
  },

  async start(): Promise<void> {
    await client.post('/automation/start');
  },

  async pause(): Promise<void> {
    await client.post('/automation/pause');
  },
};

// ── YouTube ──────────────────────────────────────────────────────────────────

export const youtubeApi = {
  async connectUrl(): Promise<string> {
    const { data } = await client.get<{ url: string } | string>('/youtube/connect');
    return typeof data === 'string' ? data : data.url;
  },

  async status(): Promise<YouTubeStatus> {
    const { data } = await client.get<YouTubeStatus>('/youtube/status');
    return data;
  },

  async disconnect(): Promise<void> {
    await client.post('/youtube/disconnect');
  },
};

// ── Games ────────────────────────────────────────────────────────────────────

export const gamesApi = {
  async list(): Promise<Game[]> {
    const { data } = await client.get<Game[]>('/games');
    return data;
  },

  async enrich(gameId: number): Promise<Game> {
    const { data } = await client.post<Game>(`/games/${gameId}/enrich`);
    return data;
  },
};

// ── Gameplays / Content ──────────────────────────────────────────────────────

export const gameplaysApi = {
  async list(includePublic = true): Promise<GameplaySource[]> {
    const { data } = await client.get<GameplaySource[]>('/sources', { params: { include_public: includePublic } });
    return data;
  },

  async getEvents(sourceId: number): Promise<{ events: GameplayEvent[] }> {
    const { data } = await client.get<{ events: GameplayEvent[] }>(`/sources/${sourceId}/events`);
    return data;
  },

  async assignGameByName(sourceId: number, gameName: string, slug?: string): Promise<void> {
    // Backend uses Form data, not JSON
    const formData = new FormData();
    formData.append('game_name', gameName);
    if (slug) formData.append('slug', slug);
    await client.post(`/sources/${sourceId}/assign-game`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  async createMappingJob(sourceId: number): Promise<void> {
    await client.post(`/gameplays/${sourceId}/create-mapping-job`);
  },

  async toggleVisibility(sourceId: number, isPublic: boolean): Promise<void> {
    await client.patch(`/gameplays/${sourceId}/visibility`, null, {
      params: { is_public: isPublic },
    });
  },

  async toggleEnabled(sourceId: number, enabled: boolean): Promise<void> {
    await client.patch(`/gameplays/${sourceId}/enabled`, null, {
      params: { enabled },
    });
  },

  async delete(sourceId: number): Promise<void> {
    await client.delete(`/sources/${sourceId}`);
  },

  async scanInbox(): Promise<{ discovered: number }> {
    const { data } = await client.post<{ discovered: number }>('/inbox/scan');
    return data;
  },

  /** Chunked upload with progress (gameplay files can be large) */
  async upload(file: { uri: string; name: string; type: string }, onProgress?: (pct: number) => void): Promise<void> {
    const formData = new FormData();
    formData.append('file', file as any);

    await client.post('/gameplays/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        if (e.total && onProgress) {
          onProgress(Math.round((e.loaded * 100) / e.total));
        }
      },
      timeout: 600000, // 10 min for large files
    });
  },
};

// ── Catalog (IGDB search) ────────────────────────────────────────────────────

export const catalogApi = {
  async search(q: string): Promise<CatalogGame[]> {
    const { data } = await client.get<CatalogGame[]>('/catalog/search', { params: { q } });
    return data;
  },

  async autocomplete(q: string): Promise<CatalogGame[] | { results: CatalogGame[] }> {
    const { data } = await client.get<CatalogGame[] | { results: CatalogGame[] }>('/catalog/autocomplete', { params: { q } });
    return data;
  },
};

// ── Jobs ─────────────────────────────────────────────────────────────────────

export const jobsApi = {
  async list(status?: string): Promise<Job[]> {
    const { data } = await client.get<Job[]>('/jobs', { params: status ? { status } : {} });
    return data;
  },

  async get(jobId: number): Promise<Job> {
    const { data } = await client.get<Job>(`/jobs/${jobId}`);
    return data;
  },
};

// ── Videos ───────────────────────────────────────────────────────────────────

export const videosApi = {
  async list(search?: string): Promise<Video[]> {
    const { data } = await client.get<Video[]>('/videos', { params: search ? { search } : {} });
    return data;
  },

  async updateMetadata(videoId: number, payload: { title?: string; description?: string; tags?: string[] }): Promise<Video> {
    const { data } = await client.put<Video>(`/videos/${videoId}/metadata`, payload);
    return data;
  },

  async publish(videoId: number, overrides?: { title?: string; description?: string; tags?: string[] }): Promise<Video> {
    const { data } = await client.post<Video>(`/videos/${videoId}/publish`, overrides || {});
    return data;
  },

  async delete(videoId: number, releaseClips = false): Promise<void> {
    await client.delete(`/videos/${videoId}`, { params: { release_clips: releaseClips } });
  },

  async regenerate(videoId: number): Promise<void> {
    await client.post(`/videos/${videoId}/regenerate`);
  },

  videoUrl,
  thumbUrl,
};

// ── Voices ───────────────────────────────────────────────────────────────────

export const voicesApi = {
  async list(): Promise<Voice[]> {
    const { data } = await client.get<Voice[]>('/voices');
    return data;
  },

  async upload(file: { uri: string; name: string; type: string }): Promise<{ filename: string }> {
    const formData = new FormData();
    formData.append('file', file as any);
    const { data } = await client.post<{ filename: string }>('/voices/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return data;
  },

  async delete(filename: string): Promise<void> {
    await client.delete(`/voices/${filename}`);
  },
};

// ── Presentation ─────────────────────────────────────────────────────────────

export const presentationApi = {
  async uploadImage(file: { uri: string; name: string; type: string }): Promise<{ key: string }> {
    const formData = new FormData();
    formData.append('file', file as any);
    const { data } = await client.post<{ key: string }>('/presentation/upload-image', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return data;
  },

  imageUrl: presentationImageUrl,
};

// ── Channel Profile ──────────────────────────────────────────────────────────

export const channelApi = {
  async getProfile(): Promise<ChannelProfile> {
    const { data } = await client.get<ChannelProfile>('/channel/profile');
    return data;
  },

  async updateProfile(payload: Partial<ChannelProfile>): Promise<void> {
    await client.put('/channel/profile', payload);
  },
};

// ── Workers ──────────────────────────────────────────────────────────────────

export const workersApi = {
  async list(): Promise<Worker[]> {
    const { data } = await client.get<Worker[]>('/workers');
    return data;
  },
};

// ── Domains ──────────────────────────────────────────────────────────────────

export const domainsApi = {
  async list(): Promise<{ domains: DomainInfo[]; current: string }> {
    const { data } = await client.get<{ domains: DomainInfo[]; current: string }>('/channel/domains');
    return data;
  },

  async reset(newDomain: string, confirm = true): Promise<{ message: string }> {
    const { data } = await client.post<{ message: string }>('/channel/reset-domain', { new_domain: newDomain, confirm });
    return data;
  },
};

// ── Knowledge Items / Ideas ──────────────────────────────────────────────────

export const knowledgeApi = {
  async list(params?: {
    item_type?: string;
    source_type?: string;
    status?: string;
    game_id?: number;
    limit?: number;
    offset?: number;
    min_score?: number;
  }): Promise<{ items: KnowledgeItem[] }> {
    const { data } = await client.get<{ items: KnowledgeItem[] }>('/knowledge-items', { params });
    return data;
  },

  async stats(): Promise<KnowledgeItemStats> {
    const { data } = await client.get<KnowledgeItemStats>('/knowledge-items/stats');
    return data;
  },

  async reject(itemId: number): Promise<void> {
    await client.post(`/knowledge-items/${itemId}/reject`);
  },

  async triggerCollection(): Promise<{ message: string }> {
    const { data } = await client.post<{ message: string }>('/knowledge-items/collect');
    return data;
  },

  async createManual(payload: { title: string; content: string; game_id?: number }): Promise<KnowledgeItem> {
    const { data } = await client.post<KnowledgeItem>('/knowledge-items', payload);
    return data;
  },

  // ── Idea Queue ────────────────────────────────────────────────────────────

  async getQueue(): Promise<{ queue: QueueEntry[]; items: KnowledgeItem[] }> {
    const { data } = await client.get<{ queue: QueueEntry[]; items: KnowledgeItem[] }>('/idea-queue');
    return data;
  },

  async addToQueue(
    knowledgeItemId: number,
    gameplayPreference?: number | null,
    reuseOverride?: string | null,
    gameplaySourceId?: number | null,
  ): Promise<{ queue: QueueEntry[]; message: string }> {
    const { data } = await client.post<{ queue: QueueEntry[]; message: string }>('/idea-queue/add', {
      knowledge_item_id: knowledgeItemId,
      gameplay_preference: gameplayPreference ?? null,
      reuse_override: reuseOverride ?? null,
      gameplay_source_id: gameplaySourceId ?? null,
    });
    return data;
  },

  async removeFromQueue(knowledgeItemId: number): Promise<{ queue: QueueEntry[]; message: string }> {
    const { data } = await client.post<{ queue: QueueEntry[]; message: string }>('/idea-queue/remove', {
      knowledge_item_id: knowledgeItemId,
    });
    return data;
  },

  async reorderQueue(orderedIds: number[]): Promise<{ queue: QueueEntry[]; message: string }> {
    const { data } = await client.post<{ queue: QueueEntry[]; message: string }>('/idea-queue/reorder', orderedIds);
    return data;
  },

  async updateQueueItem(
    knowledgeItemId: number,
    gameplayPreference?: number | null,
    reuseOverride?: string | null,
    gameplaySourceId?: number | null,
  ): Promise<{ queue: QueueEntry[]; message: string }> {
    const { data } = await client.post<{ queue: QueueEntry[]; message: string }>('/idea-queue/update', {
      knowledge_item_id: knowledgeItemId,
      gameplay_preference: gameplayPreference ?? null,
      reuse_override: reuseOverride ?? null,
      gameplay_source_id: gameplaySourceId ?? null,
    });
    return data;
  },

  // ── Gameplay Availability ─────────────────────────────────────────────────

  async getGameplayAvailability(): Promise<{ games: GameAvailability[]; max_uses: number }> {
    const { data } = await client.get<{ games: GameAvailability[]; max_uses: number }>('/gameplay-availability');
    return data;
  },

  async getGameplaySourcesForGame(gameId: number): Promise<{ game_id: number; sources: GameplaySourceInfo[]; min_free_seconds: number }> {
    const { data } = await client.get<{ game_id: number; sources: GameplaySourceInfo[]; min_free_seconds: number }>(`/gameplay-availability/${gameId}/sources`);
    return data;
  },

  // ── Collection Focus ──────────────────────────────────────────────────────

  async getCollectionFocus(): Promise<{ collection_focus: CollectionFocus | null }> {
    const { data } = await client.get<{ collection_focus: CollectionFocus | null }>('/channel/collection-focus');
    return data;
  },

  async setCollectionFocus(payload: {
    type: 'game' | 'topic' | 'game+topic';
    game_id?: number;
    game_name?: string;
    topic?: string;
    item_types?: string[];
  }): Promise<{ message: string }> {
    const { data } = await client.post<{ message: string }>('/channel/collection-focus', payload);
    return data;
  },

  async clearCollectionFocus(): Promise<{ message: string }> {
    const { data } = await client.delete<{ message: string }>('/channel/collection-focus');
    return data;
  },

  // ── Current Job (automation) ──────────────────────────────────────────────

  async getCurrentJob(): Promise<{ job: Job | null }> {
    const { data } = await client.get<{ job: Job | null }>('/automation/current-job');
    return data;
  },
};

// ── Kids ─────────────────────────────────────────────────────────────────────

export const kidsApi = {
  // ── Topics ──────────────────────────────────────────────────────────────
  async listTopics(): Promise<KidsTopic[]> {
    const { data } = await client.get<KidsTopic[]>('/kids/topics');
    return data;
  },

  async createTopic(payload: { name: string; description?: string }): Promise<KidsTopic> {
    const { data } = await client.post<KidsTopic>('/kids/topics', payload);
    return data;
  },

  async deleteTopic(topicId: number): Promise<void> {
    await client.delete(`/kids/topics/${topicId}`);
  },

  // ── Library Assets ──────────────────────────────────────────────────────
  async listLibraryAssets(params?: {
    media_kind?: string;
    status?: string;
    topic_id?: number;
    include_public?: boolean;
  }): Promise<KidsLibraryAsset[]> {
    const { data } = await client.get<KidsLibraryAsset[]>('/kids/assets', { params });
    return data;
  },

  async uploadAsset(
    file: { uri: string; name: string; type: string },
    metadata?: { tags?: string; description?: string; topic_id?: number },
    onProgress?: (pct: number) => void,
  ): Promise<KidsLibraryAsset> {
    const formData = new FormData();
    formData.append('file', file as any);
    if (metadata?.tags) formData.append('tags', metadata.tags);
    if (metadata?.description) formData.append('description', metadata.description);
    if (metadata?.topic_id) formData.append('topic_id', String(metadata.topic_id));
    const { data } = await client.post<KidsLibraryAsset>('/kids/assets/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        if (e.total && onProgress) {
          onProgress(Math.round((e.loaded * 100) / e.total));
        }
      },
      timeout: 600000,
    });
    return data;
  },

  async patchAsset(assetId: number, payload: {
    tags?: string;
    description?: string;
    topic_id?: number;
    is_public?: boolean;
  }): Promise<KidsLibraryAsset> {
    const { data } = await client.patch<KidsLibraryAsset>(`/kids/assets/${assetId}`, payload);
    return data;
  },

  async deleteAsset(assetId: number): Promise<void> {
    await client.delete(`/kids/assets/${assetId}`);
  },

  async getAssetEvents(assetId: number): Promise<{ events: GameplayEvent[] }> {
    const { data } = await client.get<{ events: GameplayEvent[] }>(`/kids/assets/${assetId}/events`);
    return data;
  },

  async createMappingJob(assetId: number): Promise<void> {
    await client.post(`/kids/assets/${assetId}/create-mapping-job`);
  },

  // ── Ideas ───────────────────────────────────────────────────────────────
  async listIdeas(params?: { status?: string; category?: string; limit?: number }): Promise<{ ideas: KidsIdea[] }> {
    const { data } = await client.get<{ ideas: KidsIdea[] }>('/kids/ideas', { params });
    return data;
  },

  async getIdeaStats(): Promise<KidsIdeaStats> {
    const { data } = await client.get<KidsIdeaStats>('/kids/ideas/stats');
    return data;
  },

  async createIdea(payload: { title: string; content: string; category: string; topic_id?: number; description?: string }): Promise<KidsIdea> {
    const { data } = await client.post<KidsIdea>('/kids/ideas', payload);
    return data;
  },

  async scoreIdea(ideaId: number): Promise<KidsIdea & { job_id?: number }> {
    const { data } = await client.post<KidsIdea & { job_id?: number }>(`/kids/ideas/${ideaId}/score`);
    return data;
  },

  async rejectIdea(ideaId: number): Promise<void> {
    await client.post(`/kids/ideas/${ideaId}/reject`);
  },

  async convertIdea(ideaId: number, topicName?: string): Promise<KidsIdea> {
    const { data } = await client.post<KidsIdea>(`/kids/ideas/${ideaId}/convert`, { topic_name: topicName });
    return data;
  },

  async produceIdea(ideaId: number): Promise<{ job_id: number; topic_id?: number; message: string }> {
    const { data } = await client.post<{ job_id: number; topic_id?: number; message: string }>(`/kids/ideas/${ideaId}/produce`);
    return data;
  },

  async discoverIdeas(payload: {
    categories?: string[];
    ideas_per_category?: number;
    include_seasonal?: boolean;
    include_topic_library?: boolean;
  }): Promise<{ discovered: number; ideas: KidsIdea[]; job_id?: number }> {
    const { data } = await client.post<{ discovered: number; ideas: KidsIdea[]; job_id?: number }>('/kids/ideas/discover', payload);
    return data;
  },

  // ── Idea Queue ──────────────────────────────────────────────────────────
  async getIdeaQueue(): Promise<{ queue: number[]; items: KidsIdea[] }> {
    const { data } = await client.get<{ queue: number[]; items: KidsIdea[] }>('/kids/idea-queue');
    return data;
  },

  async addToIdeaQueue(ideaId: number): Promise<{ queue: number[]; message: string }> {
    const { data } = await client.post<{ queue: number[]; message: string }>('/kids/idea-queue/add', { idea_id: ideaId });
    return data;
  },

  async removeFromIdeaQueue(ideaId: number): Promise<{ queue: number[]; message: string }> {
    const { data } = await client.post<{ queue: number[]; message: string }>('/kids/idea-queue/remove', { idea_id: ideaId });
    return data;
  },

  async reorderIdeaQueue(orderedIds: number[]): Promise<{ queue: number[]; message: string }> {
    const { data } = await client.post<{ queue: number[]; message: string }>('/kids/idea-queue/reorder', { idea_ids: orderedIds });
    return data;
  },

  async reconcileIdeaQueue(): Promise<{ queue: number[]; message: string }> {
    const { data } = await client.post<{ queue: number[]; message: string }>('/kids/idea-queue/reconcile');
    return data;
  },

  // ── Topic Library & Seasonal ────────────────────────────────────────────
  async getTopicLibrary(): Promise<TopicLibraryEntry[]> {
    const { data } = await client.get<TopicLibraryEntry[]>('/kids/topic-library');
    return data;
  },

  async getSeasonalCalendar(): Promise<SeasonalCalendarEntry[]> {
    const { data } = await client.get<SeasonalCalendarEntry[]>('/kids/seasonal-calendar');
    return data;
  },
};

// ── App Version ──────────────────────────────────────────────────────────────

export const appApi = {
  /** Check latest app version (public, no auth needed) */
  async getVersion(): Promise<AppVersionInfo> {
    const { data } = await client.get<AppVersionInfo>('/app/version');
    return data;
  },
};
