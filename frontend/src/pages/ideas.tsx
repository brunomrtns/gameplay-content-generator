import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui";

interface KnowledgeItem {
  id: number;
  game_id: number | null;
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
}

interface Stats {
  total: number;
  fresh: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  by_source: Record<string, number>;
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

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // "manual" is a source_type filter, not item_type — handle specially
      const isManualFilter = filterType === "manual";
      const [itemsRes, statsRes, queueRes] = await Promise.all([
        api.listKnowledgeItems({
          item_type: isManualFilter ? undefined : filterType || undefined,
          status: filterStatus || undefined,
          limit: 100,
        }),
        api.getKnowledgeItemStats(),
        api.getIdeaQueue(),
      ]);
      let allItems = itemsRes.items || [];
      if (isManualFilter) {
        allItems = allItems.filter((i: KnowledgeItem) => i.source_type === "manual");
      }
      setItems(allItems);
      setStats(statsRes);
      setQueue(queueRes.items || []);
      setQueueIds(new Set(queueRes.queue || []));
    } catch (e: any) {
      setError(e.message || "Failed to load content ideas");
    } finally {
      setLoading(false);
    }
  }, [filterType, filterStatus]);

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

  const handleAddToQueue = async (id: number) => {
    try {
      await api.addToIdeaQueue(id);
      setQueueIds((prev) => new Set(prev).add(id));
      // Update queue display
      const item = items.find((i) => i.id === id);
      if (item) setQueue((prev) => [...prev, item]);
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
    } catch (e: any) {
      setError(e.message);
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
      loadData(); // Revert on error
    }
  };

  const scoreColor = (score: number) => {
    if (score >= 70) return "text-green-400";
    if (score >= 50) return "text-yellow-400";
    if (score >= 30) return "text-orange-400";
    return "text-red-400";
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
            {collecting ? "Coletando..." : "Coletar Agora"}
          </button>
        </div>
      </div>

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
                className="flex items-center gap-3 rounded-lg border border-border bg-bg-card p-3"
              >
                <div className="flex flex-col gap-0.5">
                  <button
                    onClick={() => handleMoveQueueItem(index, "up")}
                    disabled={index === 0}
                    className="text-text-muted hover:text-text disabled:opacity-20"
                    title="Mover para cima"
                  >
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                    </svg>
                  </button>
                  <button
                    onClick={() => handleMoveQueueItem(index, "down")}
                    disabled={index === queue.length - 1}
                    className="text-text-muted hover:text-text disabled:opacity-20"
                    title="Mover para baixo"
                  >
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
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
                      <span className="text-xs text-text-muted">
                        jogo-specific
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-text line-clamp-1">{item.title}</p>
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
            Quando a fila esvazia, o sistema volta a decidir automaticamente.
          </p>
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-3">
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
      </div>

      {/* Items List */}
      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner className="h-8 w-8" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-border bg-bg-card p-8 text-center text-text-muted">
          Nenhuma ideia de conteúdo encontrada. Clique em "Coletar Agora" para buscar notícias.
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <div
              key={item.id}
              className={`rounded-lg border bg-bg-card p-4 transition-colors ${
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
                      onClick={() => handleAddToQueue(item.id)}
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
    </div>
  );
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
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <div className="text-xs text-text-muted">{label}</div>
      <div className={`text-2xl font-bold ${colorClass}`}>{value}</div>
    </div>
  );
}
