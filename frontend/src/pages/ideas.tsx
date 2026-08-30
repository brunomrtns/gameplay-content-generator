import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { useLiveData } from "@/hooks/useLiveData";
import { Spinner } from "@/components/ui";

interface KnowledgeItem {
  id: number;
  game_id: number | null;
  game_name: string | null;
  title: string;
  content: string;
  item_type: string;
  source_type: string;
  source_url: string | null;
  source_name: string | null;
  published_at: string | null;
  collected_at: string;
  editorial_score: number;
  status: string;
  franchise: string | null;
  developer: string | null;
  tags: string[];
  gameplay_preference?: number | null;
  reuse_override?: string | null;
  gameplay_source_id?: number | null;
  gameplay_source_filename?: string | null;
}

interface Stats {
  total: number;
  fresh: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  by_source: Record<string, number>;
}

interface GameAvailability {
  game_id: number;
  game_name: string;
  ownership: "own" | "public";
  availability: "abundant" | "partial" | "low" | "none" | "reuse_only";
  total_sources: number;
  available_seconds: number;
  used_seconds: number;
  eligible_events: number;
  total_events: number;
}

const TYPE_LABELS: Record<string, string> = {
  news: "Notícia",
  curiosity: "Curiosidade",
  lore: "Lore",
  fact: "Fact",
  manual: "Manual",
};

const TYPE_COLORS: Record<string, string> = {
  news: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  curiosity: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  lore: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  fact: "bg-teal-500/20 text-teal-300 border-teal-500/30",
  manual: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
};

const STATUS_LABELS: Record<string, string> = {
  fresh: "Disponível",
  used: "Usado",
  rejected: "Rejeitado",
};

/** Shorten a gameplay source filename for display in badges/tags.
 *  Removes extension, truncates long names at a reasonable length. */
function shortSourceName(filename: string | null | undefined, maxLen: number = 30): string {
  if (!filename) return "Gameplay";
  // Remove extension
  let name = filename.replace(/\.[^.]+$/, "");
  // Remove common video site prefixes/suffixes (YouTube IDs in brackets, etc.)
  name = name.replace(/\s*[｜|].*$/, "").replace(/\s*\[[^\]]*\]\s*$/, "").trim();
  if (name.length > maxLen) {
    name = name.slice(0, maxLen - 1).trim() + "…";
  }
  return name || "Gameplay";
}

const STATUS_COLORS: Record<string, string> = {
  fresh: "bg-green-500/20 text-green-300 border-green-500/30",
  used: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  rejected: "bg-red-500/20 text-red-300 border-red-500/30",
};

export function IdeasPage() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [queue, setQueue] = useState<KnowledgeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterType, setFilterType] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("fresh");
  const [collecting, setCollecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queueIds, setQueueIds] = useState<Set<number>>(new Set());
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newIdeaTitle, setNewIdeaTitle] = useState("");
  const [newIdeaContent, setNewIdeaContent] = useState("");
  const [creating, setCreating] = useState(false);
  // Advanced filters
  const [minScore, setMinScore] = useState<number>(0);
  const [searchText, setSearchText] = useState<string>("");
  const [onlyWithGameplay, setOnlyWithGameplay] = useState<boolean>(false);
  const [filterGameId, setFilterGameId] = useState<number | null>(null);
  // V3: Gameplay preference modal
  const [availability, setAvailability] = useState<GameAvailability[]>([]);
  const [queueModalItem, setQueueModalItem] = useState<KnowledgeItem | null>(null);
  const [selectedGameplay, setSelectedGameplay] = useState<number | null>(null);
  const [selectedReuseOverride, setSelectedReuseOverride] = useState<string | null>(null);
  // V4: Specific source selection (optional, within chosen game)
  const [availableSources, setAvailableSources] = useState<any[]>([]);
  const [selectedSource, setSelectedSource] = useState<number | null>(null);
  const [loadingSources, setLoadingSources] = useState(false);
  // V3: Currently processing job
  const [currentJob, setCurrentJob] = useState<any>(null);
  // Edit game for existing queue item — shows only games with gameplay available
  const [editQueueGameItem, setEditQueueGameItem] = useState<KnowledgeItem | null>(null);
  const [editSelectedGameplay, setEditSelectedGameplay] = useState<number | null>(null);
  // V4: Edit modal source selection
  const [editAvailableSources, setEditAvailableSources] = useState<any[]>([]);
  const [editSelectedSource, setEditSelectedSource] = useState<number | null>(null);
  const [editLoadingSources, setEditLoadingSources] = useState(false);
  // Collection Focus (campaign direction)
  const [focus, setFocus] = useState<any>(null);
  const [showFocusModal, setShowFocusModal] = useState(false);
  const [focusType, setFocusType] = useState<"game" | "topic" | "game+topic">("game");
  const [focusGameSearch, setFocusGameSearch] = useState("");
  const [focusGameResults, setFocusGameResults] = useState<any[]>([]);
  const [focusSelectedGame, setFocusSelectedGame] = useState<any | null>(null);
  const [focusTopic, setFocusTopic] = useState("");
  const [focusItemTypes, setFocusItemTypes] = useState<string[]>([]);
  const [focusSaving, setFocusSaving] = useState(false);

  // Live data via SSE event invalidation (replaces polling)
  const { data: currentJobData } = useLiveData<{ job: any }>(['current-job'], () => api.getCurrentJob(), ['job.status_changed']);
  const { data: queueData, refetch: refetchIdeaQueue } = useLiveData(['idea-queue'], () => api.getIdeaQueue(), ['idea_queue.updated']);

  // Sync live data to local state
  useEffect(() => {
    if (currentJobData) {
      setCurrentJob(currentJobData.job);
    }
  }, [currentJobData]);

  useEffect(() => {
    if (queueData) {
      setQueue(queueData.items || []);
      const qIds = (queueData.queue || []).map((q: any) => (typeof q === "object" ? q.ki_id : q));
      setQueueIds(new Set(qIds));
    }
  }, [queueData]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // "manual" is a source_type filter, not item_type — handle server-side
      const isManualFilter = filterType === "manual";
      const [itemsRes, statsRes, availRes, focusRes] = await Promise.all([
        api.listKnowledgeItems({
          item_type: isManualFilter ? undefined : filterType || undefined,
          source_type: isManualFilter ? "manual" : undefined,
          status: filterStatus || undefined,
          limit: 200,
          min_score: minScore > 0 ? minScore : undefined,
          game_id: filterGameId ?? undefined,
        }),
        api.getKnowledgeItemStats(),
        api.getGameplayAvailability(),
        api.getCollectionFocus(),
      ]);
      let allItems: KnowledgeItem[] = itemsRes.items || [];
      // Client-side: filter by gameplay availability
      if (onlyWithGameplay && availRes.games) {
        const gamesWithGameplay = new Set(
          (availRes.games as GameAvailability[])
            .filter((g) => g.availability !== "none" && g.availability !== "reuse_only")
            .map((g) => g.game_id),
        );
        // Include items without game_id (general curiosities — always usable)
        allItems = allItems.filter((i) => !i.game_id || gamesWithGameplay.has(i.game_id));
      }
      // Client-side: filter by search text
      if (searchText.trim()) {
        const q = searchText.toLowerCase().trim();
        allItems = allItems.filter((i) =>
          i.title?.toLowerCase().includes(q) ||
          i.content?.toLowerCase().includes(q) ||
          i.game_name?.toLowerCase().includes(q),
        );
      }
      setItems(allItems);
      setStats(statsRes);
      setAvailability(availRes.games || []);
      setFocus(focusRes.collection_focus);
    } catch (e: any) {
      setError(e.message || "Failed to load content ideas");
    } finally {
      setLoading(false);
    }
  }, [filterType, filterStatus, minScore, filterGameId, onlyWithGameplay, searchText]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleReject = async (id: number) => {
    try {
      await api.rejectKnowledgeItem(id);
      setItems((prev) => prev.filter((i) => i.id !== id));
      setQueueIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      setQueue((prev) => prev.filter((i) => i.id !== id));
      refetchIdeaQueue();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleCollect = async () => {
    setCollecting(true);
    setError(null);
    try {
      await api.triggerContentCollection();
      setTimeout(() => loadData(), 3000);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCollecting(false);
    }
  };

  const handleCreateIdea = async () => {
    if (!newIdeaTitle.trim() || !newIdeaContent.trim()) {
      setError("Título e conteúdo são obrigatórios");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      await api.createManualIdea({
        title: newIdeaTitle.trim(),
        content: newIdeaContent.trim(),
      });
      setNewIdeaTitle("");
      setNewIdeaContent("");
      setShowCreateForm(false);
      loadData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  // V3: Open gameplay preference modal when adding to queue
  const openQueueModal = (item: KnowledgeItem) => {
    setQueueModalItem(item);
    setSelectedGameplay(null);
    setSelectedReuseOverride(null);
    setAvailableSources([]);
    setSelectedSource(null);
  };

  // V4: When user selects a game, fetch its eligible sources (≥3min free)
  const handleGameplaySelect = async (gameId: number | null) => {
    setSelectedGameplay(gameId);
    setSelectedReuseOverride(null);
    setAvailableSources([]);
    setSelectedSource(null);
    if (gameId) {
      setLoadingSources(true);
      try {
        const res = await api.getGameplaySourcesForGame(gameId);
        setAvailableSources(res.sources || []);
      } catch {
        setAvailableSources([]);
      } finally {
        setLoadingSources(false);
      }
    }
  };

  const handleAddToQueue = async () => {
    if (!queueModalItem) return;
    try {
      await api.addToIdeaQueue(
        queueModalItem.id,
        selectedGameplay,
        selectedReuseOverride,
        selectedSource,
      );
      setQueueIds((prev) => new Set(prev).add(queueModalItem.id));
      // Update queue display
      setQueue((prev) => [...prev, {
        ...queueModalItem,
        gameplay_preference: selectedGameplay,
        reuse_override: selectedReuseOverride,
        gameplay_source_id: selectedSource,
      }]);
      setQueueModalItem(null);
      setSelectedGameplay(null);
      setSelectedReuseOverride(null);
      setAvailableSources([]);
      setSelectedSource(null);
      refetchIdeaQueue();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleRemoveFromQueue = async (id: number) => {
    try {
      await api.removeFromIdeaQueue(id);
      setQueueIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      setQueue((prev) => prev.filter((i) => i.id !== id));
      refetchIdeaQueue();
    } catch (e: any) {
      setError(e.message);
    }
  };

  // Update game for an existing queue item (only games with gameplay available)
  const handleUpdateQueueGame = async () => {
    if (!editQueueGameItem) return;
    try {
      await api.updateIdeaQueueItem(
        editQueueGameItem.id,
        editSelectedGameplay, // null = auto, game_id = specific
        editQueueGameItem.reuse_override ?? null,
        editSelectedSource,
      );
      // Update local state
      setQueue((prev) =>
        prev.map((i) =>
          i.id === editQueueGameItem.id
            ? { ...i, gameplay_preference: editSelectedGameplay, gameplay_source_id: editSelectedSource }
            : i,
        ),
      );
      setEditQueueGameItem(null);
      setEditSelectedGameplay(null);
      setEditAvailableSources([]);
      setEditSelectedSource(null);
      refetchIdeaQueue();
    } catch (e: any) {
      setError(e.message);
    }
  };

  // V4: When user changes game in edit modal, fetch sources
  const handleEditGameplaySelect = async (gameId: number | null) => {
    setEditSelectedGameplay(gameId);
    setEditAvailableSources([]);
    setEditSelectedSource(null);
    if (gameId) {
      setEditLoadingSources(true);
      try {
        const res = await api.getGameplaySourcesForGame(gameId);
        setEditAvailableSources(res.sources || []);
      } catch {
        setEditAvailableSources([]);
      } finally {
        setEditLoadingSources(false);
      }
    }
  };

  // Open edit modal — preselect current game and source
  const openEditQueueGame = (item: KnowledgeItem) => {
    setEditQueueGameItem(item);
    setEditSelectedGameplay(item.gameplay_preference ?? null);
    setEditSelectedSource(item.gameplay_source_id ?? null);
    setEditAvailableSources([]);
    // Pre-load sources if a game is already selected
    if (item.gameplay_preference) {
      setEditLoadingSources(true);
      api.getGameplaySourcesForGame(item.gameplay_preference)
        .then((res) => setEditAvailableSources(res.sources || []))
        .catch(() => setEditAvailableSources([]))
        .finally(() => setEditLoadingSources(false));
    }
  };

  const handleMoveQueueItem = async (index: number, direction: "up" | "down") => {
    const newQueue = [...queue];
    const swapIndex = direction === "up" ? index - 1 : index + 1;
    if (swapIndex < 0 || swapIndex >= newQueue.length) return;
    [newQueue[index], newQueue[swapIndex]] = [newQueue[swapIndex], newQueue[index]];
    setQueue(newQueue);
    try {
      await api.reorderIdeaQueue(newQueue.map((i) => i.id));
    } catch (e: any) {
      setError(e.message);
      refetchIdeaQueue(); // Revert queue on error
    }
  };

  // V3: Drag-and-drop reorder
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  const handleDragStart = (index: number) => {
    setDragIndex(index);
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    if (dragIndex === null || dragIndex === index) return;
    setDragOverIndex(index);
  };

  const handleDragEnd = () => {
    if (dragIndex === null || dragOverIndex === null || dragIndex === dragOverIndex) {
      setDragIndex(null);
      setDragOverIndex(null);
      return;
    }
    const newQueue = [...queue];
    const [moved] = newQueue.splice(dragIndex, 1);
    newQueue.splice(dragOverIndex, 0, moved);
    setQueue(newQueue);
    setDragIndex(null);
    setDragOverIndex(null);
    api.reorderIdeaQueue(newQueue.map((i) => i.id)).catch((e: any) => {
      setError(e.message);
      refetchIdeaQueue(); // Revert queue on error
    });
  };

  const scoreColor = (score: number) => {
    if (score >= 70) return "text-green-400";
    if (score >= 50) return "text-yellow-400";
    if (score >= 30) return "text-orange-400";
    return "text-red-400";
  };

  // ── Collection Focus handlers ──────────────────────────────────────────
  const handleFocusGameSearch = async (q: string) => {
    setFocusGameSearch(q);
    if (q.length < 2) {
      setFocusGameResults([]);
      return;
    }
    try {
      const res = await api.searchCatalog(q, 8);
      setFocusGameResults(res.results || []);
    } catch {
      setFocusGameResults([]);
    }
  };

  const handleSelectFocusGame = (game: any) => {
    setFocusSelectedGame(game);
    setFocusGameSearch(game.name);
    setFocusGameResults([]);
  };

  const handleSaveFocus = async () => {
    setFocusSaving(true);
    setError(null);
    try {
      const payload: any = { type: focusType };
      if (focusType === "game" || focusType === "game+topic") {
        if (!focusSelectedGame) {
          setError("Selecione um jogo do catálogo");
          setFocusSaving(false);
          return;
        }
        payload.game_id = focusSelectedGame.id;
        payload.game_name = focusSelectedGame.name;
      }
      if (focusType === "topic" || focusType === "game+topic") {
        if (!focusTopic.trim()) {
          setError("Digite um tema para a coleta");
          setFocusSaving(false);
          return;
        }
        payload.topic = focusTopic.trim();
      }
      if (focusItemTypes.length > 0) {
        payload.item_types = focusItemTypes;
      }
      await api.setCollectionFocus(payload);
      setShowFocusModal(false);
      // Reset modal state
      setFocusGameSearch("");
      setFocusGameResults([]);
      setFocusSelectedGame(null);
      setFocusTopic("");
      setFocusItemTypes([]);
      loadData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setFocusSaving(false);
    }
  };

  const handleClearFocus = async () => {
    try {
      await api.clearCollectionFocus();
      setFocus(null);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const openFocusModal = () => {
    // Pre-fill with current focus if any
    if (focus) {
      setFocusType(focus.type || "game");
      if (focus.game_id) {
        setFocusSelectedGame({ id: focus.game_id, name: focus.game_name });
        setFocusGameSearch(focus.game_name || "");
      }
      if (focus.topic) setFocusTopic(focus.topic);
      if (focus.item_types) setFocusItemTypes(focus.item_types);
    } else {
      setFocusType("game");
      setFocusGameSearch("");
      setFocusGameResults([]);
      setFocusSelectedGame(null);
      setFocusTopic("");
      setFocusItemTypes([]);
    }
    setShowFocusModal(true);
  };

  const toggleFocusItemType = (t: string) => {
    setFocusItemTypes((prev) =>
      prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
    );
  };

  const focusLabel = (f: any): string => {
    if (!f) return "";
    if (f.type === "game") return `Jogo: ${f.game_name}`;
    if (f.type === "topic") return `Tema: ${f.topic}`;
    if (f.type === "game+topic") return `${f.game_name} + ${f.topic}`;
    return "";
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text">Content Ideas</h1>
          <p className="text-sm text-text-muted mt-1">
            Banco de ideias de conteúdo (notícias, curiosidades, lore) coletadas automaticamente
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowCreateForm(!showCreateForm)}
            className="rounded-lg border border-teal-500/30 bg-teal-500/10 px-4 py-2 text-sm font-medium text-teal-300 hover:bg-teal-500/20"
          >
            + Nova Ideia
          </button>
          <button
            onClick={handleCollect}
            disabled={collecting}
            className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-500 disabled:opacity-50"
          >
            {collecting ? "Coletando..." : focus ? `Coletar (foco: ${focusLabel(focus)})` : "Coletar Agora"}
          </button>
        </div>
      </div>

      {/* Collection Focus Bar */}
      <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3">
        <svg className="h-5 w-5 text-amber-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
        </svg>
        {focus ? (
          <>
            <div className="flex-1 min-w-0">
              <span className="text-xs text-text-muted">Foco de coleta ativo</span>
              <p className="text-sm font-medium text-text truncate">
                {focusLabel(focus)}
                {focus.item_types && focus.item_types.length > 0 && (
                  <span className="ml-2 text-xs text-text-muted">
                    ({focus.item_types.join(", ")})
                  </span>
                )}
              </p>
            </div>
            <button
              onClick={openFocusModal}
              className="rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:text-text hover:border-text-muted"
            >
              Editar
            </button>
            <button
              onClick={handleClearFocus}
              className="rounded-lg border border-red-500/30 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/10"
            >
              Remover
            </button>
          </>
        ) : (
          <>
            <div className="flex-1 min-w-0">
              <span className="text-sm text-text-muted">
                Sem foco — coleta geral (derivada do gameplay inventory + perfil editorial)
              </span>
            </div>
            <button
              onClick={openFocusModal}
              className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-500/20"
            >
              Definir Foco
            </button>
          </>
        )}
      </div>

      {/* Collection Focus Modal */}
      {showFocusModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowFocusModal(false)}>
          <div
            className="w-full max-w-lg rounded-xl border border-border bg-surface p-6 space-y-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-bold text-text">Foco de Coleta</h2>
              <button
                onClick={() => setShowFocusModal(false)}
                className="text-text-muted hover:text-text"
              >
                ✕
              </button>
            </div>
            <p className="text-sm text-text-muted">
              Direcione a busca de ideias para um jogo, tema, ou ambos. As ideias coletadas aparecem na lista normal — o foco só decide o QUE buscar.
            </p>

            {/* Focus type selector */}
            <div>
              <label className="block text-xs font-medium text-text-muted mb-2">Tipo de foco</label>
              <div className="grid grid-cols-3 gap-2">
                {([
                  { v: "game", label: "Jogo" },
                  { v: "topic", label: "Tema livre" },
                  { v: "game+topic", label: "Jogo + Tema" },
                ] as const).map((opt) => (
                  <button
                    key={opt.v}
                    onClick={() => setFocusType(opt.v)}
                    className={`rounded-lg border px-3 py-2 text-sm font-medium transition ${
                      focusType === opt.v
                        ? "border-amber-500 bg-amber-500/20 text-amber-300"
                        : "border-border text-text-muted hover:text-text hover:border-text-muted"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Game selector (for game / game+topic) */}
            {(focusType === "game" || focusType === "game+topic") && (
              <div className="relative">
                <label className="block text-xs font-medium text-text-muted mb-2">Jogo</label>
                <input
                  type="text"
                  placeholder="Buscar jogo no catálogo..."
                  value={focusGameSearch}
                  onChange={(e) => handleFocusGameSearch(e.target.value)}
                  className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-amber-500/40"
                />
                {focusGameResults.length > 0 && (
                  <div className="absolute z-10 mt-1 w-full rounded-lg border border-border bg-surface shadow-lg max-h-60 overflow-y-auto">
                    {focusGameResults.map((g) => (
                      <button
                        key={g.id}
                        onClick={() => handleSelectFocusGame(g)}
                        className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-bg"
                      >
                        {g.cover_url && (
                          <img src={g.cover_url} alt="" className="h-8 w-8 rounded object-cover" />
                        )}
                        <div className="min-w-0">
                          <p className="text-sm text-text truncate">{g.name}</p>
                          {g.release_year && (
                            <p className="text-xs text-text-muted">{g.release_year}</p>
                          )}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
                {focusSelectedGame && (
                  <p className="mt-2 text-xs text-green-400">
                    Selecionado: {focusSelectedGame.name}
                  </p>
                )}
              </div>
            )}

            {/* Topic input (for topic / game+topic) */}
            {(focusType === "topic" || focusType === "game+topic") && (
              <div>
                <label className="block text-xs font-medium text-text-muted mb-2">Tema</label>
                <input
                  type="text"
                  placeholder="Ex: mistérios do oceano, fatos sobre espaço, crime organizado..."
                  value={focusTopic}
                  onChange={(e) => setFocusTopic(e.target.value)}
                  className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-amber-500/40"
                />
              </div>
            )}

            {/* Item types filter (optional) */}
            <div>
              <label className="block text-xs font-medium text-text-muted mb-2">
                Tipos de ideia (opcional — vazio = todos)
              </label>
              <div className="flex flex-wrap gap-2">
                {(["news", "curiosity", "lore", "fact"] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => toggleFocusItemType(t)}
                    className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                      focusItemTypes.includes(t)
                        ? "border-amber-500 bg-amber-500/20 text-amber-300"
                        : "border-border text-text-muted hover:text-text"
                    }`}
                  >
                    {TYPE_LABELS[t] || t}
                  </button>
                ))}
              </div>
            </div>

            {/* Actions */}
            <div className="flex gap-2 justify-end pt-2">
              <button
                onClick={() => setShowFocusModal(false)}
                className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text"
              >
                Cancelar
              </button>
              <button
                onClick={handleSaveFocus}
                disabled={focusSaving}
                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
              >
                {focusSaving ? "Salvando..." : "Salvar Foco"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Manual Idea Form */}
      {showCreateForm && (
        <div className="rounded-lg border border-teal-500/30 bg-teal-500/5 p-4 space-y-3">
          <h3 className="font-semibold text-text">Nova Ideia Manual</h3>
          <input
            type="text"
            placeholder="Título da ideia"
            value={newIdeaTitle}
            onChange={(e) => setNewIdeaTitle(e.target.value)}
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40"
          />
          <textarea
            placeholder="Descreva a ideia de conteúdo..."
            value={newIdeaContent}
            onChange={(e) => setNewIdeaContent(e.target.value)}
            rows={4}
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 resize-none"
          />
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => {
                setShowCreateForm(false);
                setNewIdeaTitle("");
                setNewIdeaContent("");
              }}
              className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text"
            >
              Cancelar
            </button>
            <button
              onClick={handleCreateIdea}
              disabled={creating || !newIdeaTitle.trim() || !newIdeaContent.trim()}
              className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-500 disabled:opacity-50"
            >
              {creating ? "Criando..." : "Criar Ideia"}
            </button>
          </div>
        </div>
      )}

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <StatCard label="Total" value={stats.total} />
          <StatCard label="Disponíveis" value={stats.fresh} accent="green" />
          <StatCard
            label="Usados"
            value={stats.by_status?.used || 0}
            accent="blue"
          />
          <StatCard
            label="Na Fila"
            value={queue.length}
            accent="purple"
          />
          <StatCard
            label="Notícias"
            value={stats.by_type?.news || 0}
            accent="blue"
          />
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* V3: Currently Processing — show above the queue when a job is running */}
      {currentJob && (
        <div className="rounded-lg border border-teal-500/40 bg-teal-500/10 p-4">
          <div className="flex items-center gap-3 mb-2">
            <div className="relative flex h-3 w-3">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-teal-400 opacity-75"></span>
              <span className="relative inline-flex h-3 w-3 rounded-full bg-teal-400"></span>
            </div>
            <h2 className="text-lg font-semibold text-text">Em Processamento</h2>
            <span className="text-sm text-text-muted">
              Job #{currentJob.id}
            </span>
          </div>
          <div className="flex items-center gap-3 rounded-lg border border-teal-500/20 bg-surface p-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-teal-500/20">
              <svg className="h-5 w-5 text-teal-300 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" style={{ animationDuration: "3s" }}>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6V4m6 6h2m-6 4h2m-6-4H4m12 8a8 8 0 100-16 8 8 0 000 16z" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              {currentJob.ki_title ? (
                <>
                  <p className="text-sm font-medium text-text line-clamp-1">
                    {currentJob.ki_title}
                  </p>
                  <div className="flex items-center gap-2 mt-1">
                    {currentJob.ki_item_type && (
                      <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                        TYPE_COLORS[currentJob.ki_item_type] || "bg-gray-500/20 text-gray-300 border-gray-500/30"
                      }`}>
                        {TYPE_LABELS[currentJob.ki_item_type] || currentJob.ki_item_type}
                      </span>
                    )}
                    <span className="text-xs text-teal-300 font-medium">
                      {currentJob.stage_label}
                    </span>
                  </div>
                </>
              ) : (
                <p className="text-sm font-medium text-text">
                  {currentJob.stage_label || "Processando..."}
                </p>
              )}
            </div>
            {currentJob.progress > 0 && (
              <div className="shrink-0 text-right">
                <div className="text-xs text-text-muted">Progresso</div>
                <div className="text-sm font-medium text-teal-300">
                  {Math.round(currentJob.progress * 100)}%
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Idea Queue Section */}
      {queue.length > 0 && (
        <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-4">
          <div className="flex items-center gap-2 mb-3">
            <svg className="h-5 w-5 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
            <h2 className="text-lg font-semibold text-text">Fila de Produção</h2>
            <span className="text-sm text-text-muted">
              {queue.length} {queue.length === 1 ? "ideia" : "ideias"} — consumidas em ordem
            </span>
          </div>
          <div className="space-y-2">
            {queue.map((item, index) => (
              <div
                key={item.id}
                draggable
                onDragStart={() => handleDragStart(index)}
                onDragOver={(e) => handleDragOver(e, index)}
                onDragEnd={handleDragEnd}
                onDrop={(e) => { e.preventDefault(); handleDragEnd(); }}
                className={`flex items-center gap-3 rounded-lg border bg-surface p-3 transition-all cursor-grab active:cursor-grabbing ${
                  dragIndex === index
                    ? "opacity-40 border-purple-500/60"
                    : dragOverIndex === index
                    ? "border-purple-500/60 bg-purple-500/10 scale-[1.01]"
                    : "border-border hover:border-purple-500/30"
                }`}
              >
                {/* Drag handle */}
                <div className="flex flex-col gap-0.5 text-text-muted">
                  <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M7 4a1 1 0 110 2 1 1 0 010-2zM7 9a1 1 0 110 2 1 1 0 010-2zM7 14a1 1 0 110 2 1 1 0 010-2zM13 4a1 1 0 110 2 1 1 0 010-2zM13 9a1 1 0 110 2 1 1 0 010-2zM13 14a1 1 0 110 2 1 1 0 010-2z" />
                  </svg>
                </div>
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-purple-500/20 text-xs font-bold text-purple-300">
                  {index + 1}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                        TYPE_COLORS[item.item_type] || "bg-gray-500/20 text-gray-300 border-gray-500/30"
                      }`}
                    >
                      {TYPE_LABELS[item.item_type] || item.item_type}
                    </span>
                    {item.game_id && (
                      <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-xs font-medium text-text-muted">
                        {item.game_name || `Jogo #${item.game_id}`}
                      </span>
                    )}
                    {/* V3: Show gameplay preference — clickable to change */}
                    {item.gameplay_preference && item.gameplay_preference > 0 && (
                      <button
                        onClick={() => openEditQueueGame(item)}
                        className="flex items-center gap-1 rounded-full border border-teal-500/30 bg-teal-500/10 px-2 py-0.5 text-xs font-medium text-teal-300 transition-colors hover:bg-teal-500/20"
                        title="Alterar jogo"
                      >
                        {availability.find((g) => g.game_id === item.gameplay_preference)?.game_name || `Jogo #${item.gameplay_preference}`}
                        <svg className="h-2.5 w-2.5 opacity-60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                        </svg>
                      </button>
                    )}
                    {/* V4: Show specific source badge if set */}
                    {item.gameplay_source_id && (
                      <span className="rounded-full border border-purple-500/30 bg-purple-500/10 px-2 py-0.5 text-xs font-medium text-purple-300" title={item.gameplay_source_filename || `Source #${item.gameplay_source_id}`}>
                        🎮 {shortSourceName(item.gameplay_source_filename)}
                      </span>
                    )}
                    {!item.gameplay_preference && !item.game_id && (
                      <button
                        onClick={() => openEditQueueGame(item)}
                        className="flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5 text-xs text-text-muted transition-colors hover:border-teal-500/40 hover:text-teal-300"
                        title="Definir jogo específico"
                      >
                        <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                        </svg>
                        Definir jogo
                      </button>
                    )}
                    {/* V3: Show reuse override */}
                    {item.reuse_override === "allow_reuse" && (
                      <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-300">
                        Reutilização excepcional
                      </span>
                    )}
                    {item.reuse_override === "skip" && (
                      <span className="rounded-full border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-300">
                        Aguardar material
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-text line-clamp-1">{item.title}</p>
                  {item.content && (
                    <p className="text-xs text-text-muted line-clamp-2 mt-0.5">{item.content}</p>
                  )}
                </div>
                <button
                  onClick={() => handleRemoveFromQueue(item.id)}
                  className="rounded-lg border border-border px-2 py-1 text-xs text-text-muted hover:border-red-500/30 hover:text-red-300"
                  title="Remover da fila"
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-text-muted">
            A automação consome estas ideias em ordem (primeiro = próximo vídeo).
            Arraste para reordenar. Quando a fila esvazia, o sistema volta a decidir automaticamente.
          </p>
        </div>
      )}

      {/* Filters */}
      <div className="space-y-3">
        {/* Row 1: Search + score + gameplay toggle */}
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="text"
            placeholder="Buscar por título, conteúdo ou jogo..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="flex-1 min-w-[200px] rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40"
          />
          {/* Score filter buttons */}
          <div className="flex items-center gap-1 rounded-lg border border-border bg-surface p-1">
            <span className="text-xs text-text-muted px-2">Score</span>
            {[
              { v: 0, label: "Todos" },
              { v: 30, label: "≥30" },
              { v: 50, label: "≥50" },
              { v: 70, label: "≥70" },
            ].map((s) => (
              <button
                key={s.v}
                onClick={() => setMinScore(s.v)}
                className={`rounded px-2 py-1 text-xs font-medium transition ${
                  minScore === s.v
                    ? "bg-accent text-white"
                    : "text-text-muted hover:text-text"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          {/* Gameplay availability toggle */}
          <button
            onClick={() => setOnlyWithGameplay(!onlyWithGameplay)}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition ${
              onlyWithGameplay
                ? "border-teal-500/40 bg-teal-500/15 text-teal-300"
                : "border-border bg-surface text-text-muted hover:text-text"
            }`}
            title="Só mostrar ideias cujo jogo tem gameplay disponível para produzir o vídeo"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            Com gameplay
          </button>
        </div>
        {/* Row 2: Type + status + game dropdown */}
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
            className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-accent/40"
          >
            <option value="">Todos os tipos</option>
            <option value="news">Notícias</option>
            <option value="curiosity">Curiosidades</option>
            <option value="lore">Lore</option>
            <option value="manual">Manuais</option>
          </select>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-accent/40"
          >
            <option value="">Todos os status</option>
            <option value="fresh">Disponíveis</option>
            <option value="used">Usados</option>
            <option value="rejected">Rejeitados</option>
          </select>
          <select
            value={filterGameId ?? ""}
            onChange={(e) => setFilterGameId(e.target.value ? Number(e.target.value) : null)}
            className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-accent/40"
          >
            <option value="">Todos os jogos</option>
            {availability
              .filter((g) => g.availability !== "none")
              .map((g) => (
                <option key={g.game_id} value={g.game_id}>
                  {g.game_name} ({g.availability})
                </option>
              ))}
          </select>
          {/* Result count */}
          <span className="text-xs text-text-muted ml-auto">
            {items.length} {items.length === 1 ? "ideia" : "ideias"}
          </span>
        </div>
      </div>

      {/* Items List */}
      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner className="h-8 w-8" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-border bg-surface p-8 text-center text-text-muted">
          Nenhuma ideia de conteúdo encontrada. Clique em "Coletar Agora" para buscar notícias.
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <div
              key={item.id}
              className={`rounded-lg border bg-surface p-4 transition-colors ${
                queueIds.has(item.id)
                  ? "border-purple-500/40 bg-purple-500/5"
                  : "border-border hover:border-teal-500/30"
              }`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-2">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                        TYPE_COLORS[item.item_type] || "bg-gray-500/20 text-gray-300 border-gray-500/30"
                      }`}
                    >
                      {TYPE_LABELS[item.item_type] || item.item_type}
                    </span>
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                        STATUS_COLORS[item.status] || "bg-gray-500/20 text-gray-300 border-gray-500/30"
                      }`}
                    >
                      {STATUS_LABELS[item.status] || item.status}
                    </span>
                    <span className={`text-sm font-bold ${scoreColor(item.editorial_score)}`}>
                      Score: {item.editorial_score.toFixed(0)}
                    </span>
                    {item.source_name && (
                      <span className="text-xs text-text-muted">via {item.source_name}</span>
                    )}
                  </div>
                  <h3 className="font-medium text-text mb-1 line-clamp-2">{item.title}</h3>
                  <p className="text-sm text-text-muted line-clamp-3">{item.content}</p>
                  {item.source_url && (
                    <a
                      href={item.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-teal-400 hover:text-teal-300 mt-2 inline-block"
                    >
                      Ver fonte →
                    </a>
                  )}
                </div>
                <div className="flex flex-col gap-2 shrink-0">
                  {item.status === "fresh" && !queueIds.has(item.id) && (
                    <button
                      onClick={() => openQueueModal(item)}
                      className="rounded-lg bg-purple-600/80 px-3 py-1.5 text-xs font-medium text-white hover:bg-purple-500"
                      title="Adicionar à fila de produção"
                    >
                      + Fila
                    </button>
                  )}
                  {queueIds.has(item.id) && (
                    <button
                      onClick={() => handleRemoveFromQueue(item.id)}
                      className="rounded-lg border border-purple-500/30 bg-purple-500/10 px-3 py-1.5 text-xs font-medium text-purple-300 hover:bg-purple-500/20"
                      title="Remover da fila"
                    >
                      Na Fila ✓
                    </button>
                  )}
                  {item.status === "fresh" && (
                    <button
                      onClick={() => handleReject(item.id)}
                      className="rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:border-red-500/30 hover:text-red-300"
                    >
                      Rejeitar
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* V3: Gameplay Preference Modal */}
      {queueModalItem && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
          onClick={() => setQueueModalItem(null)}
        >
          <div
            className="w-full max-w-md rounded-xl border border-border-bright bg-surface-elevated p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold text-text mb-1">Adicionar à Fila</h3>
            <p className="text-sm text-text-muted mb-4 line-clamp-2">
              {queueModalItem.title}
            </p>

            {/* Gameplay selector */}
            <label className="block text-sm font-medium text-text mb-2">
              Gameplay de fundo
            </label>
            <select
              value={selectedGameplay ?? ""}
              onChange={(e) => handleGameplaySelect(e.target.value ? Number(e.target.value) : null)}
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-accent/40 mb-3"
            >
              <option value="">Automático (sistema escolhe)</option>
              {availability.map((g) => (
                <option key={g.game_id} value={g.game_id}>
                  {g.game_name} · {ownershipLabel(g.ownership)} · {availabilityLabel(g.availability)}
                </option>
              ))}
            </select>

            {/* V4: Specific source selector (optional) */}
            {selectedGameplay && (
              <div className="mb-3">
                {loadingSources ? (
                  <p className="text-xs text-text-muted">Carregando gameplays disponíveis…</p>
                ) : (
                  <>
                    <label className="block text-sm font-medium text-text mb-1.5">
                      Gameplay específica (opcional)
                    </label>
                    <select
                      value={selectedSource ?? ""}
                      onChange={(e) => setSelectedSource(e.target.value ? Number(e.target.value) : null)}
                      className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-accent/40"
                    >
                      <option value="">Usar todas as gameplays do jogo (recomendado)</option>
                      {availableSources.map((s) => (
                        <option key={s.source_id} value={s.source_id}>
                          {(s.filename || `Source #${s.source_id}`).slice(0, 50)} · {Math.floor(s.free_seconds / 60)}min livre · {s.eligible_events}/{s.total_events} eventos
                        </option>
                      ))}
                    </select>
                    {availableSources.length > 0 ? (
                      <>
                        <p className="text-xs text-text-muted mt-1">
                          Restringe os cortes a uma única gameplay. O padrão usa todas as gameplays do jogo, com mais variedade de cenas.
                        </p>
                        {selectedSource && (() => {
                          const src = availableSources.find((s) => s.source_id === selectedSource);
                          if (!src) return null;
                          const freeMin = Math.floor(src.free_seconds / 60);
                          const freeSec = Math.floor(src.free_seconds % 60);
                          const isLow = src.free_seconds < 180; // < 3 min
                          const isVeryLow = src.free_seconds < 120; // < 2 min
                          if (!isLow) return null;
                          return (
                            <div className={`mt-2 rounded-lg border p-2.5 ${isVeryLow ? "border-red-500/30 bg-red-500/10" : "border-amber-500/30 bg-amber-500/10"}`}>
                              <p className={`text-xs ${isVeryLow ? "text-red-300" : "text-amber-300"}`}>
                                {isVeryLow ? "⚠" : "⚠"} Apenas {freeMin}:{freeSec.toString().padStart(2, "0")} de material livre.
                                {" "}Pode acabar antes da sua vez na fila — se isso acontecer, o sistema usará outra gameplay do mesmo jogo automaticamente.
                              </p>
                            </div>
                          );
                        })()}
                      </>
                    ) : (
                      <p className="text-xs text-text-muted mt-1">
                        Nenhuma gameplay individual tem 2 min de material livre no momento — o sistema usará todas as gameplays do jogo automaticamente, o que é o comportamento padrão.
                      </p>
                    )}
                  </>
                )}
              </div>
            )}

            {/* Availability badges for selected game */}
            {selectedGameplay && (() => {
              const game = availability.find((g) => g.game_id === selectedGameplay);
              if (!game) return null;
              const isLow = game.availability === "none" || game.availability === "low" || game.availability === "reuse_only";
              return (
                <div className="mb-4 space-y-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${availabilityColor(game.availability)}`}>
                      {availabilityLabel(game.availability)}
                    </span>
                    <span className="text-xs text-text-muted">
                      {ownershipLabel(game.ownership)}
                    </span>
                    <span className="text-xs text-text-muted">
                      {game.eligible_events}/{game.total_events} eventos
                    </span>
                  </div>
                  {isLow && (
                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 space-y-2">
                      <p className="text-xs text-amber-300">
                        Pouco material elegível disponível para este jogo.
                        O que fazer?
                      </p>
                      <div className="space-y-1.5">
                        <label className="flex items-center gap-2 text-sm text-text cursor-pointer">
                          <input
                            type="radio"
                            name="reuse-override"
                            value=""
                            checked={selectedReuseOverride === null}
                            onChange={() => setSelectedReuseOverride(null)}
                          />
                          Usar outra gameplay automaticamente (fallback)
                        </label>
                        <label className="flex items-center gap-2 text-sm text-text cursor-pointer">
                          <input
                            type="radio"
                            name="reuse-override"
                            value="allow_reuse"
                            checked={selectedReuseOverride === "allow_reuse"}
                            onChange={() => setSelectedReuseOverride("allow_reuse")}
                          />
                          Permitir reutilização excepcional nesta ideia
                        </label>
                        <label className="flex items-center gap-2 text-sm text-text cursor-pointer">
                          <input
                            type="radio"
                            name="reuse-override"
                            value="skip"
                            checked={selectedReuseOverride === "skip"}
                            onChange={() => setSelectedReuseOverride("skip")}
                          />
                          Não gerar enquanto não houver material elegível
                        </label>
                      </div>
                    </div>
                  )}
                </div>
              );
            })()}

            <div className="flex gap-2 justify-end mt-4">
              <button
                onClick={() => setQueueModalItem(null)}
                className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text"
              >
                Cancelar
              </button>
              <button
                onClick={handleAddToQueue}
                className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-medium text-white hover:bg-purple-500"
              >
                Adicionar à Fila
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit game for existing queue item — only games with gameplay available */}
      {editQueueGameItem && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
          onClick={() => setEditQueueGameItem(null)}
        >
          <div
            className="w-full max-w-md rounded-xl border border-border-bright bg-surface-elevated p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold text-text mb-1">Alterar Jogo</h3>
            <p className="text-sm text-text-muted mb-4 line-clamp-2">
              {editQueueGameItem.title}
            </p>

            <label className="block text-sm font-medium text-text mb-2">
              Gameplay de fundo
            </label>
            <select
              value={editSelectedGameplay ?? ""}
              onChange={(e) => handleEditGameplaySelect(e.target.value ? Number(e.target.value) : null)}
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-accent/40 mb-3"
            >
              <option value="">Automático (sistema escolhe)</option>
              {availability.map((g) => (
                <option key={g.game_id} value={g.game_id}>
                  {g.game_name} · {ownershipLabel(g.ownership)} · {availabilityLabel(g.availability)}
                </option>
              ))}
            </select>

            {/* V4: Specific source selector in edit modal */}
            {editSelectedGameplay && (
              <div className="mb-3">
                {editLoadingSources ? (
                  <p className="text-xs text-text-muted">Carregando gameplays disponíveis…</p>
                ) : (
                  <>
                    <label className="block text-sm font-medium text-text mb-1.5">
                      Gameplay específica (opcional)
                    </label>
                    <select
                      value={editSelectedSource ?? ""}
                      onChange={(e) => setEditSelectedSource(e.target.value ? Number(e.target.value) : null)}
                      className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-accent/40"
                    >
                      <option value="">Usar todas as gameplays do jogo (recomendado)</option>
                      {editAvailableSources.map((s) => (
                        <option key={s.source_id} value={s.source_id}>
                          {(s.filename || `Source #${s.source_id}`).slice(0, 50)} · {Math.floor(s.free_seconds / 60)}min livre · {s.eligible_events}/{s.total_events} eventos
                        </option>
                      ))}
                    </select>
                    {editAvailableSources.length > 0 ? (
                      <p className="text-xs text-text-muted mt-1">
                        Restringe os cortes a uma única gameplay. O padrão usa todas as gameplays do jogo, com mais variedade de cenas.
                      </p>
                    ) : (
                      <p className="text-xs text-text-muted mt-1">
                        Nenhuma gameplay individual tem 2 min de material livre no momento — o sistema usará todas as gameplays do jogo automaticamente, o que é o comportamento padrão.
                      </p>
                    )}
                  </>
                )}
              </div>
            )}

            {editSelectedGameplay && (() => {
              const game = availability.find((g) => g.game_id === editSelectedGameplay);
              if (!game) return null;
              return (
                <div className="mb-4 flex items-center gap-2 flex-wrap">
                  <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${availabilityColor(game.availability)}`}>
                    {availabilityLabel(game.availability)}
                  </span>
                  <span className="text-xs text-text-muted">
                    {ownershipLabel(game.ownership)}
                  </span>
                  <span className="text-xs text-text-muted">
                    {game.eligible_events}/{game.total_events} eventos
                  </span>
                </div>
              );
            })()}

            <div className="flex gap-2 justify-end mt-4">
              <button
                onClick={() => setEditQueueGameItem(null)}
                className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text"
              >
                Cancelar
              </button>
              <button
                onClick={handleUpdateQueueGame}
                className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-500"
              >
                Salvar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// V3: Helper functions for availability display
function ownershipLabel(ownership: string): string {
  return ownership === "own" ? "Própria" : "Pública";
}

function availabilityLabel(status: string): string {
  const labels: Record<string, string> = {
    abundant: "Bastante material",
    partial: "Material parcial",
    low: "Pouco material",
    none: "Sem material novo",
    reuse_only: "Apenas reutilização",
  };
  return labels[status] || status;
}

function availabilityColor(status: string): string {
  const colors: Record<string, string> = {
    abundant: "bg-green-500/20 text-green-300 border-green-500/30",
    partial: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
    low: "bg-orange-500/20 text-orange-300 border-orange-500/30",
    none: "bg-red-500/20 text-red-300 border-red-500/30",
    reuse_only: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  };
  return colors[status] || "bg-gray-500/20 text-gray-300 border-gray-500/30";
}

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: "green" | "blue" | "purple";
}) {
  const colorClass =
    accent === "green"
      ? "text-green-400"
      : accent === "blue"
      ? "text-blue-400"
      : accent === "purple"
      ? "text-purple-400"
      : "text-text";
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="text-xs text-text-muted">{label}</div>
      <div className={`text-2xl font-bold ${colorClass}`}>{value}</div>
    </div>
  );
}
