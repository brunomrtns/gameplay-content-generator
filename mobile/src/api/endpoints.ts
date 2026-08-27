import { client, saveToken, getSSOCookies, clearAuth, videoUrl, thumbUrl, presentationImageUrl } from './client';

// ── Auth ─────────────────────────────────────────────────────────────────────

export const authApi = {
  /** Exchange SSO cookies for JWT token (mobile auth flow) */
  async exchangeToken(): Promise<{ token: string; user: any }> {
    const cookies = await getSSOCookies();
    if (!cookies) throw new Error('No SSO cookies found');
    const { data } = await client.post('/auth/token', cookies);
    await saveToken(data.token, data.user);
    return data;
  },

  async getMe(): Promise<any> {
    const { data } = await client.get('/auth/me');
    return data;
  },

  async logout(): Promise<void> {
    try {
      await client.post('/auth/logout');
    } catch {}
    await clearAuth();
  },

  async listUsers(): Promise<any[]> {
    const { data } = await client.get('/auth/users');
    return data;
  },

  async updateUser(userId: number, payload: { name?: string; is_active?: boolean }): Promise<any> {
    const { data } = await client.put(`/auth/users/${userId}`, payload);
    return data;
  },

  async deleteUser(userId: number): Promise<void> {
    await client.delete(`/auth/users/${userId}`);
  },

  // ── Onboarding ─────────────────────────────────────────────────────────
  async getOnboarding(): Promise<{ completed: boolean }> {
    const { data } = await client.get('/auth/onboarding');
    return data;
  },

  async completeOnboarding(): Promise<{ completed: boolean }> {
    const { data } = await client.post('/auth/onboarding/complete');
    return data;
  },

  async resetOnboarding(): Promise<{ completed: boolean }> {
    const { data } = await client.post('/auth/onboarding/reset');
    return data;
  },
};

// ── Dashboard ────────────────────────────────────────────────────────────────

export const dashboardApi = {
  async get(): Promise<any> {
    const { data } = await client.get('/dashboard');
    return data;
  },
};

// ── Automation ───────────────────────────────────────────────────────────────

export const automationApi = {
  async get(): Promise<any> {
    const { data } = await client.get('/automation');
    return data;
  },

  async update(config: any): Promise<void> {
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
    const { data } = await client.get('/youtube/connect');
    return data.url || data;
  },

  async status(): Promise<any> {
    const { data } = await client.get('/youtube/status');
    return data;
  },

  async disconnect(): Promise<void> {
    await client.post('/youtube/disconnect');
  },
};

// ── Games ────────────────────────────────────────────────────────────────────

export const gamesApi = {
  async list(): Promise<any[]> {
    const { data } = await client.get('/games');
    return data;
  },

  async enrich(gameId: number): Promise<any> {
    const { data } = await client.post(`/games/${gameId}/enrich`);
    return data;
  },
};

// ── Gameplays / Content ──────────────────────────────────────────────────────

export const gameplaysApi = {
  async list(includePublic = true): Promise<any[]> {
    const { data } = await client.get('/sources', { params: { include_public: includePublic } });
    return data;
  },

  async getEvents(sourceId: number): Promise<{ events: any[] }> {
    const { data } = await client.get(`/sources/${sourceId}/events`);
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
    const { data } = await client.post('/inbox/scan');
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
  async search(q: string): Promise<any[]> {
    const { data } = await client.get('/catalog/search', { params: { q } });
    return data;
  },

  async autocomplete(q: string): Promise<any[]> {
    const { data } = await client.get('/catalog/autocomplete', { params: { q } });
    return data;
  },
};

// ── Jobs ─────────────────────────────────────────────────────────────────────

export const jobsApi = {
  async list(status?: string): Promise<any[]> {
    const { data } = await client.get('/jobs', { params: status ? { status } : {} });
    return data;
  },

  async get(jobId: number): Promise<any> {
    const { data } = await client.get(`/jobs/${jobId}`);
    return data;
  },
};

// ── Videos ───────────────────────────────────────────────────────────────────

export const videosApi = {
  async list(search?: string): Promise<any[]> {
    const { data } = await client.get('/videos', { params: search ? { search } : {} });
    return data;
  },

  async updateMetadata(videoId: number, payload: { title?: string; description?: string; tags?: string[] }): Promise<any> {
    const { data } = await client.put(`/videos/${videoId}/metadata`, payload);
    return data;
  },

  async publish(videoId: number, overrides?: { title?: string; description?: string; tags?: string[] }): Promise<any> {
    const { data } = await client.post(`/videos/${videoId}/publish`, overrides || {});
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
  async list(): Promise<any[]> {
    const { data } = await client.get('/voices');
    return data;
  },

  async upload(file: { uri: string; name: string; type: string }): Promise<any> {
    const formData = new FormData();
    formData.append('file', file as any);
    const { data } = await client.post('/voices/upload', formData, {
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
    const { data } = await client.post('/presentation/upload-image', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return data;
  },

  imageUrl: presentationImageUrl,
};

// ── Channel Profile ──────────────────────────────────────────────────────────

export const channelApi = {
  async getProfile(): Promise<any> {
    const { data } = await client.get('/channel/profile');
    return data;
  },

  async updateProfile(payload: any): Promise<void> {
    await client.patch('/channel/profile', payload);
  },
};

// ── Workers ──────────────────────────────────────────────────────────────────

export const workersApi = {
  async list(): Promise<any[]> {
    const { data } = await client.get('/workers');
    return data;
  },
};

// ── Domains ──────────────────────────────────────────────────────────────────

export const domainsApi = {
  async list(): Promise<{ domains: any[]; current: string }> {
    const { data } = await client.get('/channel/domains');
    return data;
  },

  async reset(newDomain: string, confirm = true): Promise<any> {
    const { data } = await client.post('/channel/reset-domain', { new_domain: newDomain, confirm });
    return data;
  },
};

// ── Knowledge Items / Ideas ──────────────────────────────────────────────────

export const knowledgeApi = {
  async list(params?: {
    item_type?: string;
    status?: string;
    game_id?: number;
    limit?: number;
    offset?: number;
    min_score?: number;
  }): Promise<{ items: any[] }> {
    const { data } = await client.get('/knowledge-items', { params });
    return data;
  },

  async stats(): Promise<any> {
    const { data } = await client.get('/knowledge-items/stats');
    return data;
  },

  async reject(itemId: number): Promise<void> {
    await client.post(`/knowledge-items/${itemId}/reject`);
  },

  async triggerCollection(): Promise<any> {
    const { data } = await client.post('/knowledge-items/collect');
    return data;
  },

  async createManual(payload: { title: string; content: string; game_id?: number }): Promise<any> {
    const { data } = await client.post('/knowledge-items', payload);
    return data;
  },

  // ── Idea Queue ────────────────────────────────────────────────────────────

  async getQueue(): Promise<{ queue: any[]; items: any[] }> {
    const { data } = await client.get('/idea-queue');
    return data;
  },

  async addToQueue(
    knowledgeItemId: number,
    gameplayPreference?: number | null,
    reuseOverride?: string | null,
    gameplaySourceId?: number | null,
  ): Promise<any> {
    const { data } = await client.post('/idea-queue/add', {
      knowledge_item_id: knowledgeItemId,
      gameplay_preference: gameplayPreference ?? null,
      reuse_override: reuseOverride ?? null,
      gameplay_source_id: gameplaySourceId ?? null,
    });
    return data;
  },

  async removeFromQueue(knowledgeItemId: number): Promise<any> {
    const { data } = await client.post('/idea-queue/remove', {
      knowledge_item_id: knowledgeItemId,
    });
    return data;
  },

  async reorderQueue(orderedIds: number[]): Promise<any> {
    const { data } = await client.post('/idea-queue/reorder', orderedIds);
    return data;
  },

  async updateQueueItem(
    knowledgeItemId: number,
    gameplayPreference?: number | null,
    reuseOverride?: string | null,
    gameplaySourceId?: number | null,
  ): Promise<any> {
    const { data } = await client.post('/idea-queue/update', {
      knowledge_item_id: knowledgeItemId,
      gameplay_preference: gameplayPreference ?? null,
      reuse_override: reuseOverride ?? null,
      gameplay_source_id: gameplaySourceId ?? null,
    });
    return data;
  },

  // ── Gameplay Availability ─────────────────────────────────────────────────

  async getGameplayAvailability(): Promise<{ games: any[]; max_uses: number }> {
    const { data } = await client.get('/gameplay-availability');
    return data;
  },

  async getGameplaySourcesForGame(gameId: number): Promise<{ game_id: number; sources: any[]; min_free_seconds: number }> {
    const { data } = await client.get(`/gameplay-availability/${gameId}/sources`);
    return data;
  },

  // ── Collection Focus ──────────────────────────────────────────────────────

  async getCollectionFocus(): Promise<{ collection_focus: any }> {
    const { data } = await client.get('/channel/collection-focus');
    return data;
  },

  async setCollectionFocus(payload: {
    type: 'game' | 'topic' | 'game+topic';
    game_id?: number;
    game_name?: string;
    topic?: string;
    item_types?: string[];
  }): Promise<any> {
    const { data } = await client.put('/channel/collection-focus', payload);
    return data;
  },

  async clearCollectionFocus(): Promise<any> {
    const { data } = await client.delete('/channel/collection-focus');
    return data;
  },

  // ── Current Job (automation) ──────────────────────────────────────────────

  async getCurrentJob(): Promise<{ job: any }> {
    const { data } = await client.get('/automation/current-job');
    return data;
  },
};

// ── Kids ─────────────────────────────────────────────────────────────────────

export const kidsApi = {
  // ── Topics ──────────────────────────────────────────────────────────────
  async listTopics(): Promise<any[]> {
    const { data } = await client.get('/kids/topics');
    return data;
  },

  async createTopic(payload: any): Promise<any> {
    const { data } = await client.post('/kids/topics', payload);
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
  }): Promise<any[]> {
    const { data } = await client.get('/kids/assets', { params });
    return data;
  },

  async uploadAsset(
    file: { uri: string; name: string; type: string },
    metadata?: { tags?: string; description?: string; topic_id?: number },
    onProgress?: (pct: number) => void,
  ): Promise<any> {
    const formData = new FormData();
    formData.append('file', file as any);
    if (metadata?.tags) formData.append('tags', metadata.tags);
    if (metadata?.description) formData.append('description', metadata.description);
    if (metadata?.topic_id) formData.append('topic_id', String(metadata.topic_id));
    const { data } = await client.post('/kids/assets/upload', formData, {
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
  }): Promise<any> {
    const { data } = await client.patch(`/kids/assets/${assetId}`, payload);
    return data;
  },

  async deleteAsset(assetId: number): Promise<void> {
    await client.delete(`/kids/assets/${assetId}`);
  },

  async getAssetEvents(assetId: number): Promise<{ events: any[] }> {
    const { data } = await client.get(`/kids/assets/${assetId}/events`);
    return data;
  },

  async createMappingJob(assetId: number): Promise<void> {
    await client.post(`/kids/assets/${assetId}/create-mapping-job`);
  },

  // ── Ideas ───────────────────────────────────────────────────────────────
  async listIdeas(params?: { status?: string; category?: string; limit?: number }): Promise<{ ideas: any[] }> {
    const { data } = await client.get('/kids/ideas', { params });
    return data;
  },

  async getIdeaStats(): Promise<any> {
    const { data } = await client.get('/kids/ideas/stats');
    return data;
  },

  async createIdea(payload: any): Promise<any> {
    const { data } = await client.post('/kids/ideas', payload);
    return data;
  },

  async scoreIdea(ideaId: number): Promise<any> {
    const { data } = await client.post(`/kids/ideas/${ideaId}/score`);
    return data;
  },

  async rejectIdea(ideaId: number): Promise<void> {
    await client.post(`/kids/ideas/${ideaId}/reject`);
  },

  async convertIdea(ideaId: number, topicName?: string): Promise<any> {
    const { data } = await client.post(`/kids/ideas/${ideaId}/convert`, { topic_name: topicName });
    return data;
  },

  async produceIdea(ideaId: number): Promise<any> {
    const { data } = await client.post(`/kids/ideas/${ideaId}/produce`);
    return data;
  },

  async discoverIdeas(payload: {
    categories?: string[];
    ideas_per_category?: number;
    include_seasonal?: boolean;
    include_topic_library?: boolean;
  }): Promise<any> {
    const { data } = await client.post('/kids/ideas/discover', payload);
    return data;
  },

  // ── Idea Queue ──────────────────────────────────────────────────────────
  async getIdeaQueue(): Promise<{ queue: number[]; items: any[] }> {
    const { data } = await client.get('/kids/idea-queue');
    return data;
  },

  async addToIdeaQueue(ideaId: number): Promise<any> {
    const { data } = await client.post('/kids/idea-queue/add', { idea_id: ideaId });
    return data;
  },

  async removeFromIdeaQueue(ideaId: number): Promise<any> {
    const { data } = await client.post('/kids/idea-queue/remove', { idea_id: ideaId });
    return data;
  },

  async reorderIdeaQueue(orderedIds: number[]): Promise<any> {
    const { data } = await client.post('/kids/idea-queue/reorder', { idea_ids: orderedIds });
    return data;
  },

  async reconcileIdeaQueue(): Promise<any> {
    const { data } = await client.post('/kids/idea-queue/reconcile');
    return data;
  },

  // ── Topic Library & Seasonal ────────────────────────────────────────────
  async getTopicLibrary(): Promise<any> {
    const { data } = await client.get('/kids/topic-library');
    return data;
  },

  async getSeasonalCalendar(): Promise<any> {
    const { data } = await client.get('/kids/seasonal-calendar');
    return data;
  },
};

// ── App distribution (self-hosted update check) ──────────────────────────────

export interface AppVersionInfo {
  available: boolean;
  version: string | null;
  versionCode: number | null;
  download_url: string | null;
  released_at: string | null;
  changelog: string | null;
  size_bytes: number | null;
}

export const appApi = {
  /** Check latest app version (public, no auth needed) */
  async getVersion(): Promise<AppVersionInfo> {
    const { data } = await client.get('/app/version');
    return data;
  },
};
