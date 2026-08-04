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
};

const TYPE_COLORS: Record<string, string> = {
  news: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  curiosity: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  lore: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  fact: "bg-teal-500/20 text-teal-300 border-teal-500/30",
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
  const [loading, setLoading] = useState(true);
  const [filterType, setFilterType] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [collecting, setCollecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [itemsRes, statsRes] = await Promise.all([
        api.listKnowledgeItems({
          item_type: filterType || undefined,
          status: filterStatus || undefined,
          limit: 100,
        }),
        api.getKnowledgeItemStats(),
      ]);
      setItems(itemsRes.items || []);
      setStats(statsRes);
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
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleCollect = async () => {
    setCollecting(true);
    setError(null);
    try {
      await api.triggerContentCollection();
      // Reload after a delay to let the job process
      setTimeout(() => loadData(), 3000);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCollecting(false);
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
        <button
          onClick={handleCollect}
          disabled={collecting}
          className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-500 disabled:opacity-50"
        >
          {collecting ? "Coletando..." : "Coletar Agora"}
        </button>
      </div>

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
            label="Notícias"
            value={stats.by_type?.news || 0}
            accent="blue"
          />
          <StatCard
            label="Curiosidades"
            value={stats.by_type?.curiosity || 0}
            accent="purple"
          />
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-3">
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="rounded-lg border border-border bg-bg-card px-3 py-2 text-sm text-text"
        >
          <option value="">Todos os tipos</option>
          <option value="news">Notícias</option>
          <option value="curiosity">Curiosidades</option>
          <option value="lore">Lore</option>
        </select>
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="rounded-lg border border-border bg-bg-card px-3 py-2 text-sm text-text"
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
              className="rounded-lg border border-border bg-bg-card p-4 hover:border-teal-500/30 transition-colors"
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
