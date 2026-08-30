import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { useLiveData } from "@/hooks/useLiveData";
import { Spinner } from "@/components/ui";
import { toast } from "sonner";
import {
  Lightbulb,
  Wand2,
  Loader2,
  Brain,
  Play,
  XCircle,
  ListChecks,
  ArrowUp,
  ArrowDown,
  X,
  RefreshCw,
  Sparkles,
  Clock,
} from "lucide-react";

const TOPIC_LIBRARY_CATEGORIES = [
  "animals",
  "science",
  "space",
  "dinosaurs",
  "nature",
  "ocean",
  "human_body",
  "history",
  "geography",
  "vehicles",
  "food",
  "colors",
  "numbers",
  "curiosity",
];

const IDEA_STATUS_COLORS: Record<string, string> = {
  discovered: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  evaluated: "bg-teal-500/20 text-teal-300 border-teal-500/30",
  queued: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  converted: "bg-green-500/20 text-green-300 border-green-500/30",
  rejected: "bg-red-500/20 text-red-300 border-red-500/30",
  expired: "bg-gray-500/20 text-gray-300 border-gray-500/30",
};

export function KidsIdeasPage() {
  const { t } = useTranslation();
  const [ideas, setIdeas] = useState<any[]>([]);
  const [queue, setQueue] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryJobId, setDiscoveryJobId] = useState<number | null>(null);
  const [scoring, setScoring] = useState<Record<number, number>>({}); // ideaId → jobId
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [showDiscover, setShowDiscover] = useState(false);
  const [discoverCategories, setDiscoverCategories] = useState<string[]>([]);
  const [discoverCount, setDiscoverCount] = useState(3);
  const [reconciling, setReconciling] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newIdeaTitle, setNewIdeaTitle] = useState("");
  const [newIdeaDescription, setNewIdeaDescription] = useState("");
  const [newIdeaCategory, setNewIdeaCategory] = useState("general");
  const [creating, setCreating] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [ideasRes, queueRes] = await Promise.all([
        api.listKidsIdeas({ status: filterStatus || undefined, limit: 100 }),
        api.getKidsIdeaQueue(),
      ]);
      setIdeas(ideasRes.ideas || []);
      setQueue(queueRes.items || []);
    } catch (err: any) {
      toast.error(err.message || t('kids:ideas.toast.loadError'));
    } finally {
      setLoading(false);
    }
  }, [filterStatus]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleDiscover = async () => {
    setDiscovering(true);
    try {
      const result = await api.discoverKidsIdeas({
        categories: discoverCategories.length > 0 ? discoverCategories : undefined,
        ideas_per_category: discoverCount,
        include_seasonal: true,
        include_topic_library: true,
      });
      setDiscoveryJobId(result.job_id);
      toast.info(t('kids:ideas.toast.discoveryQueued', { jobId: result.job_id }));
      setShowDiscover(false);
    } catch (err: any) {
      toast.error(err.message || t('kids:ideas.toast.discoveryError'));
      setDiscovering(false);
    }
  };

  const handleScore = async (id: number) => {
    try {
      const result = await api.scoreKidsIdea(id);
      const jobId = result.job_id;
      if (!jobId) {
        toast.error(t('kids:ideas.toast.scoreScheduleError'));
        return;
      }
      setScoring((prev) => ({ ...prev, [id]: jobId }));
      toast.info(t('kids:ideas.toast.scoreQueued', { jobId }));
    } catch (err: any) {
      toast.error(err.message || t('kids:ideas.toast.scoreError'));
    }
  };

  const handleReject = async (id: number) => {
    try {
      await api.rejectKidsIdea(id);
      toast.success(t('kids:ideas.toast.rejected'));
      await loadData();
    } catch (err: any) {
      toast.error(err.message || t('kids:ideas.toast.rejectError'));
    }
  };

  const handleCreateIdea = async () => {
    if (!newIdeaTitle.trim()) {
      toast.error(t('kids:ideas.toast.titleRequired'));
      return;
    }
    setCreating(true);
    try {
      await api.createKidsIdea({
        title: newIdeaTitle.trim(),
        description: newIdeaDescription.trim(),
        category: newIdeaCategory,
      });
      toast.success(t('kids:ideas.toast.created'));
      setNewIdeaTitle("");
      setNewIdeaDescription("");
      setNewIdeaCategory("general");
      setShowCreateForm(false);
      await loadData();
    } catch (err: any) {
      toast.error(err.message || t('kids:ideas.toast.createError'));
    } finally {
      setCreating(false);
    }
  };

  const handleProduce = async (id: number) => {
    try {
      const result = await api.produceKidsIdea(id);
      toast.success(t('kids:ideas.toast.produced', { jobId: result.job_id, topicId: result.topic_id }));
      await loadData();
    } catch (err: any) {
      toast.error(err.message || t('kids:ideas.toast.produceError'));
    }
  };

  const handleAddToQueue = async (id: number) => {
    try {
      await api.addKidsIdeaToQueue(id);
      toast.success(t('kids:ideas.toast.addedToQueue'));
      await loadData();
    } catch (err: any) {
      toast.error(err.message || t('kids:ideas.toast.addToQueueError'));
    }
  };

  const handleRemoveFromQueue = async (id: number) => {
    try {
      await api.removeKidsIdeaFromQueue(id);
      toast.success(t('kids:ideas.toast.removedFromQueue'));
      await loadData();
    } catch (err: any) {
      toast.error(err.message || t('kids:ideas.toast.removeFromQueueError'));
    }
  };

  const handleReconcile = async () => {
    setReconciling(true);
    try {
      await api.reconcileKidsIdeaQueue();
      toast.success(t('kids:ideas.toast.reconciled'));
      await loadData();
    } catch (err: any) {
      toast.error(err.message || t('kids:ideas.toast.reconcileError'));
    } finally {
      setReconciling(false);
    }
  };

  const handleMoveQueueItem = async (index: number, direction: "up" | "down") => {
    const newQueue = [...queue];
    const swapIndex = direction === "up" ? index - 1 : index + 1;
    if (swapIndex < 0 || swapIndex >= newQueue.length) return;
    [newQueue[index], newQueue[swapIndex]] = [newQueue[swapIndex], newQueue[index]];
    setQueue(newQueue);
    try {
      await api.reorderKidsIdeaQueue(newQueue.map((q) => q.id));
    } catch (err: any) {
      toast.error(err.message || t('kids:ideas.toast.reorderError'));
      loadData();
    }
  };

  // Drag-and-drop reorder
  const handleDragStart = (index: number) => setDragIndex(index);
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
    api.reorderKidsIdeaQueue(newQueue.map((q) => q.id)).catch((e: any) => {
      toast.error(e.message || t('kids:ideas.toast.reorderError'));
      loadData();
    });
  };

  const queueIds = new Set(queue.map((q) => q.id));

  const scoreColor = (score: number) => {
    if (score >= 70) return "text-green-400";
    if (score >= 50) return "text-yellow-400";
    if (score >= 30) return "text-orange-400";
    return "text-red-400";
  };

  return (
    <div className="space-y-6">
      {/* Job watchers — useLiveData polls job status via SSE event invalidation */}
      {discoveryJobId && (
        <JobWatcher
          jobId={discoveryJobId}
          onCompleted={(job) => {
            const created = job.artifacts?.created_count ?? 0;
            const skipped = job.artifacts?.skipped_count ?? 0;
            toast.success(t('kids:ideas.toast.discoveryCompleted', { created, skipped }));
            setDiscoveryJobId(null);
            setDiscovering(false);
            loadData();
          }}
          onFailed={(job) => {
            toast.error(t('kids:ideas.toast.discoveryFailed', { error: job.error || t('kids:ideas.toast.unknownError') }));
            setDiscoveryJobId(null);
            setDiscovering(false);
          }}
        />
      )}
      {Object.entries(scoring).map(([ideaIdStr, jobId]) => (
        <JobWatcher
          key={jobId}
          jobId={jobId}
          onCompleted={() => {
            toast.success(t('kids:ideas.toast.scored', { id: ideaIdStr }));
            setScoring((prev) => {
              const next = { ...prev };
              delete next[Number(ideaIdStr)];
              return next;
            });
            loadData();
          }}
          onFailed={(job) => {
            toast.error(t('kids:ideas.toast.scoreFailed', { id: ideaIdStr, error: job.error || "" }));
            setScoring((prev) => {
              const next = { ...prev };
              delete next[Number(ideaIdStr)];
              return next;
            });
          }}
        />
      ))}
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text">{t('kids:ideas.title')}</h1>
          <p className="text-sm text-text-muted mt-1">
            {t('kids:ideas.subtitle')}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowCreateForm(!showCreateForm)}
            className="rounded-lg border border-teal-500/30 bg-teal-500/10 px-4 py-2 text-sm font-medium text-teal-300 hover:bg-teal-500/20"
          >
            {t('kids:ideas.newIdea')}
          </button>
          <button
            onClick={() => setShowDiscover(!showDiscover)}
            className="rounded-lg border border-accent/30 bg-accent/10 px-4 py-2 text-sm font-medium text-accent hover:bg-accent/20"
          >
            <Wand2 className="h-4 w-4 inline mr-1" /> {t('kids:ideas.discover')}
          </button>
        </div>
      </div>

      {/* Create Manual Idea Form */}
      {showCreateForm && (
        <div className="rounded-lg border border-teal-500/30 bg-teal-500/5 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-text">{t('kids:ideas.manualTitle')}</h3>
            <button
              onClick={() => {
                setShowCreateForm(false);
                setNewIdeaTitle("");
                setNewIdeaDescription("");
                setNewIdeaCategory("general");
              }}
              className="text-text-muted hover:text-text"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <input
            type="text"
            placeholder={t('kids:ideas.titlePlaceholder')}
            value={newIdeaTitle}
            onChange={(e) => setNewIdeaTitle(e.target.value)}
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40"
          />
          <textarea
            placeholder={t('kids:ideas.descPlaceholder')}
            value={newIdeaDescription}
            onChange={(e) => setNewIdeaDescription(e.target.value)}
            rows={3}
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent/40 resize-none"
          />
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1">{t('kids:ideas.category')}</label>
            <select
              value={newIdeaCategory}
              onChange={(e) => setNewIdeaCategory(e.target.value)}
              className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-accent/40"
            >
              {TOPIC_LIBRARY_CATEGORIES.map((c) => (
                <option key={c} value={c}>{t(`kids:category.${c}`)}</option>
              ))}
              <option value="general">{t('kids:category.general')}</option>
            </select>
          </div>
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => {
                setShowCreateForm(false);
                setNewIdeaTitle("");
                setNewIdeaDescription("");
                setNewIdeaCategory("general");
              }}
              className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text"
            >
              {t('common:cancel')}
            </button>
            <button
              onClick={handleCreateIdea}
              disabled={creating || !newIdeaTitle.trim()}
              className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-500 disabled:opacity-50"
            >
              {creating ? t('kids:ideas.creating') : t('kids:ideas.createIdea')}
            </button>
          </div>
        </div>
      )}

      {/* Discovery Panel */}
      {showDiscover && (
        <div className="rounded-lg border border-accent/30 bg-accent/5 p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-text">{t('kids:ideas.discoveryTitle')}</h3>
            <button onClick={() => setShowDiscover(false)} className="text-text-muted hover:text-text">
              <X className="h-4 w-4" />
            </button>
          </div>
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-2">
              {t('kids:ideas.categoriesLabel')}
            </label>
            <div className="flex flex-wrap gap-2">
              {TOPIC_LIBRARY_CATEGORIES.map((c) => (
                <button
                  key={c}
                  onClick={() => {
                    setDiscoverCategories((prev) =>
                      prev.includes(c)
                        ? prev.filter((v) => v !== c)
                        : [...prev, c],
                    );
                  }}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                    discoverCategories.includes(c)
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border bg-surface text-text-muted hover:text-text"
                  }`}
                >
                  {t(`kids:category.${c}`)}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-text-secondary">
              {t('kids:ideas.ideasPerCategory', { count: discoverCount })}
            </label>
            <input
              type="range"
              min={1}
              max={10}
              value={discoverCount}
              onChange={(e) => setDiscoverCount(Number(e.target.value))}
              className="mt-1 w-full"
            />
          </div>
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => setShowDiscover(false)}
              className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text"
            >
              {t('common:cancel')}
            </button>
            <button
              onClick={handleDiscover}
              disabled={discovering}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
            >
              {discovering ? (
                <span className="flex items-center gap-1.5">
                  <Clock className="h-3.5 w-3.5 animate-pulse" />
                  {t('kids:ideas.jobQueued')}
                </span>
              ) : (
                t('kids:ideas.discover')
              )}
            </button>
          </div>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label={t('kids:ideas.stats.total')} value={ideas.length} />
        <StatCard label={t('kids:ideas.stats.inQueue')} value={queue.length} accent="purple" />
        <StatCard label={t('kids:ideas.stats.evaluated')} value={ideas.filter((i) => i.status === "evaluated").length} accent="green" />
        <StatCard label={t('kids:ideas.stats.discovered')} value={ideas.filter((i) => i.status === "discovered").length} accent="blue" />
      </div>

      {/* Idea Queue Section — same pattern as Games ideas.tsx */}
      {queue.length > 0 && (
        <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-4">
          <div className="flex items-center gap-2 mb-3">
            <ListChecks className="h-5 w-5 text-purple-400" />
            <h2 className="text-lg font-semibold text-text">{t('kids:ideas.queue.title')}</h2>
            <span className="text-sm text-text-muted">
              {t('kids:ideas.queue.count', { count: queue.length })}
            </span>
            <button
              onClick={handleReconcile}
              disabled={reconciling}
              className="ml-auto rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:text-text hover:border-accent/40"
              title={t('kids:ideas.queue.reconcileTitle')}
            >
              {reconciling ? <Loader2 className="h-3.5 w-3.5 animate-spin inline" /> : <RefreshCw className="h-3.5 w-3.5 inline" />}
              {t('kids:ideas.queue.reconcile')}
            </button>
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
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    {item.category && (
                      <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-xs font-medium text-text-muted">
                        {item.category}
                      </span>
                    )}
                    {item.editorial_score !== null && item.editorial_score !== undefined && (
                      <span className={`text-sm font-bold ${scoreColor(item.editorial_score)}`}>
                        Score: {item.editorial_score.toFixed(0)}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-text line-clamp-1">{item.title}</p>
                </div>
                <button
                  onClick={() => handleRemoveFromQueue(item.id)}
                  className="rounded-lg border border-border px-2 py-1 text-xs text-text-muted hover:border-red-500/30 hover:text-red-300"
                  title={t('kids:ideas.queue.removeFromQueue')}
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-text-muted">
            {t('kids:ideas.queue.hint')}
          </p>
        </div>
      )}

      {/* Filter */}
      <div className="flex gap-3">
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-accent/40"
        >
          <option value="">{t('kids:ideas.filter.allStatus')}</option>
          <option value="discovered">{t('kids:ideas.status.discovered')}</option>
          <option value="evaluated">{t('kids:ideas.status.evaluated')}</option>
          <option value="queued">{t('kids:ideas.status.queued')}</option>
          <option value="converted">{t('kids:ideas.status.converted')}</option>
          <option value="rejected">{t('kids:ideas.status.rejected')}</option>
          <option value="expired">{t('kids:ideas.status.expired')}</option>
        </select>
      </div>

      {/* Ideas List */}
      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner className="h-8 w-8" />
        </div>
      ) : ideas.length === 0 ? (
        <div className="rounded-lg border border-border bg-surface p-8 text-center text-text-muted">
          {t('kids:ideas.empty')}
        </div>
      ) : (
        <div className="space-y-3">
          {ideas.map((idea) => (
            <div
              key={idea.id}
              className={`rounded-lg border bg-surface p-4 transition-colors ${
                queueIds.has(idea.id)
                  ? "border-purple-500/40 bg-purple-500/5"
                  : "border-border hover:border-accent/30"
              }`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-2 flex-wrap">
                    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${IDEA_STATUS_COLORS[idea.status] || ""}`}>
                      {t(`kids:ideas.status.${idea.status}`)}
                    </span>
                    <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-xs font-medium text-text-muted">
                      {t(`kids:ideas.source.${idea.source}`)}
                    </span>
                    {idea.category && (
                      <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-xs font-medium text-text-muted">
                        {idea.category}
                      </span>
                    )}
                    {idea.editorial_score !== null && idea.editorial_score !== undefined && (
                      <span className={`text-sm font-bold ${scoreColor(idea.editorial_score)}`}>
                        Score: {idea.editorial_score.toFixed(0)}
                      </span>
                    )}
                    {idea.safety_score !== null && idea.safety_score !== undefined && (
                      <span className={`text-xs ${idea.safety_score >= 0.7 ? "text-green-400" : "text-red-400"}`}>
                        Safety: {(idea.safety_score * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                  <h3 className="font-medium text-text mb-1 line-clamp-2">{idea.title}</h3>
                  {idea.description && (
                    <p className="text-sm text-text-muted line-clamp-3">{idea.description}</p>
                  )}
                  {idea.safety_flags && idea.safety_flags.length > 0 && (
                    <p className="text-xs text-red-400 mt-1">
                      Flags: {idea.safety_flags.join(", ")}
                    </p>
                  )}
                </div>
                <div className="flex flex-col gap-2 shrink-0">
                  {idea.status === "discovered" && (
                    <button
                      onClick={() => handleScore(idea.id)}
                      disabled={!!scoring[idea.id]}
                      className="rounded-lg bg-accent/80 px-3 py-1.5 text-xs font-medium text-white hover:bg-accent disabled:opacity-50"
                    >
                      {scoring[idea.id] ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Brain className="h-3.5 w-3.5" />}
                      {" "}{t('kids:ideas.actions.evaluate')}
                    </button>
                  )}
                  {idea.status === "evaluated" && !queueIds.has(idea.id) && (
                    <button
                      onClick={() => handleAddToQueue(idea.id)}
                      className="rounded-lg bg-purple-600/80 px-3 py-1.5 text-xs font-medium text-white hover:bg-purple-500"
                      title={t('kids:ideas.actions.addToQueueTitle')}
                    >
                      {t('kids:ideas.actions.addToQueue')}
                    </button>
                  )}
                  {queueIds.has(idea.id) && (
                    <button
                      onClick={() => handleRemoveFromQueue(idea.id)}
                      className="rounded-lg border border-purple-500/30 bg-purple-500/10 px-3 py-1.5 text-xs font-medium text-purple-300 hover:bg-purple-500/20"
                      title={t('kids:ideas.actions.removeFromQueueTitle')}
                    >
                      {t('kids:ideas.actions.inQueue')}
                    </button>
                  )}
                  {(idea.status === "evaluated" || idea.status === "converted") && (
                    <button
                      onClick={() => handleProduce(idea.id)}
                      className="rounded-lg border border-accent/30 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20"
                      title={t('kids:ideas.actions.produceTitle')}
                    >
                      <Play className="h-3.5 w-3.5" /> {t('kids:ideas.actions.produce')}
                    </button>
                  )}
                  {(idea.status === "discovered" || idea.status === "evaluated" || idea.status === "queued") && (
                    <button
                      onClick={() => handleReject(idea.id)}
                      className="rounded-lg border border-border px-3 py-1.5 text-xs text-text-muted hover:border-red-500/30 hover:text-red-300"
                    >
                      {t('kids:ideas.actions.reject')}
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

function StatCard({ label, value, accent }: { label: string; value: number; accent?: "green" | "blue" | "purple" }) {
  const colorClass =
    accent === "green" ? "text-green-400"
    : accent === "blue" ? "text-blue-400"
    : accent === "purple" ? "text-purple-400"
    : "text-text";
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="text-xs text-text-muted">{label}</div>
      <div className={`text-2xl font-bold ${colorClass}`}>{value}</div>
    </div>
  );
}

// ── JobWatcher — polls a single job via SSE event invalidation ──────────────
// Replaces the manual setInterval polling. Each active job gets its own
// watcher; when the job completes or fails, the callback fires once.
function JobWatcher({
  jobId,
  onCompleted,
  onFailed,
}: {
  jobId: number;
  onCompleted: (job: any) => void;
  onFailed: (job: any) => void;
}) {
  const { data: job } = useLiveData<any>(
    ["kids-job", jobId],
    () => api.getJob(jobId),
    ["job.status_changed", "kids_idea.updated"],
  );

  const completedRef = useCallbackRef(onCompleted);
  const failedRef = useCallbackRef(onFailed);

  useEffect(() => {
    if (!job) return;
    if (job.status === "completed") completedRef(job);
    else if (job.status === "failed") failedRef(job);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status]);

  return null;
}

// Stable callback ref — keeps the latest callback without re-triggering the effect
function useCallbackRef<T extends (...args: any[]) => void>(fn: T): T {
  const ref = useRef(fn);
  ref.current = fn;
  return useCallback(((...args: any[]) => ref.current(...args)) as T, []);
}
