import { useState, useCallback } from "react";
import { useTranslation, Trans } from "react-i18next";
import { api } from "@/lib/api";
import { useDomain } from "@/lib/domain-config";
import { useLiveData } from "@/hooks/useLiveData";
import { Badge, Card, Spinner, EmptyState } from "@/components/ui";
import { fmtDate, fmtDuration } from "@/lib/utils";
import {
  Video as VideoIcon,
  Play,
  Search,
  Film,
  CheckCircle2,
  XCircle,
  Youtube,
  Upload,
  X,
  ExternalLink,
  Loader2,
  AlertCircle,
  Trash2,
  RotateCcw,
  Lightbulb,
  Gamepad2,
  Clapperboard,
  FileText,
  Sparkles,
} from "lucide-react";

const VIDEO_STATUS_CONFIG: Record<
  string,
  { variant: "default" | "success" | "warning" | "error" | "info" }
> = {
  pending: { variant: "default" },
  ready: { variant: "info" },
  qa_passed: { variant: "success" },
  qa_failed: { variant: "error" },
  pending_approval: { variant: "warning" },
  published: { variant: "success" },
  publish_failed: { variant: "error" },
};

// V3: Helper — can publish from modal (any non-published status with a file)
const canPublishModal = (v: any) =>
  v.storage_key && v.status !== "published";

export function VideosPage() {
  const { t } = useTranslation();
  const { config } = useDomain();
  const { data: videos, isLoading, refetch } = useLiveData(['videos'], () => api.listVideos(), ['video.created', 'video.updated']);
  const isKidsDomain = config.id === "kids";
  const [search, setSearch] = useState("");
  const [playing, setPlaying] = useState<any | null>(null);
  const [publishing, setPublishing] = useState<number | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const [confirmRelease, setConfirmRelease] = useState<number | null>(null);
  const [confirmRegenerate, setConfirmRegenerate] = useState<any | null>(null);
  const [regenerating, setRegenerating] = useState<number | null>(null);
  // V3: Editable metadata in modal
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editTags, setEditTags] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [savingMeta, setSavingMeta] = useState(false);

  const filtered = videos?.filter((v: any) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      (v.social_title || "").toLowerCase().includes(q) ||
      (v.topic || "").toLowerCase().includes(q)
    );
  });

  const handlePublish = useCallback(
    async (id: number, overrides?: { title?: string; description?: string; tags?: string[] }) => {
      setPublishing(id);
      setPublishError(null);
      try {
        await api.publishVideo(id, overrides);
        await refetch();
        setPlaying(null);
      } catch (e: any) {
        setPublishError(e.message || t("videos:errors.publish"));
      } finally {
        setPublishing(null);
      }
    },
    [refetch, t]
  );

  // V3: Open modal and initialize edit fields from video data
  const openModal = useCallback((v: any) => {
    setPlaying(v);
    setEditTitle(v.social_title || v.topic || "");
    setEditDescription(v.social_description || "");
    setEditTags((v.social_tags || []).join(", "));
    setEditMode(false);
  }, []);

  // V3: Save metadata edits
  const handleSaveMetadata = useCallback(
    async (id: number) => {
      setSavingMeta(true);
      setPublishError(null);
      try {
        const tags = editTags
          .split(",")
          .map((t) => t.trim().replace(/^#/, ""))
          .filter(Boolean);
        await api.updateVideoMetadata(id, {
          title: editTitle,
          description: editDescription,
          tags,
        });
        await refetch();
        setEditMode(false);
      } catch (e: any) {
        setPublishError(e.message || t("videos:errors.saveMeta"));
      } finally {
        setSavingMeta(false);
      }
    },
    [editTitle, editDescription, editTags, refetch, t]
  );

  // V3: Publish from modal — sends edited metadata as overrides
  const handlePublishFromModal = useCallback(
    (id: number) => {
      const tags = editTags
        .split(",")
        .map((t) => t.trim().replace(/^#/, ""))
        .filter(Boolean);
      handlePublish(id, {
        title: editTitle,
        description: editDescription,
        tags,
      });
    },
    [editTitle, editDescription, editTags, handlePublish]
  );

  const handleDelete = useCallback(
    async (id: number, releaseClips: boolean) => {
      setDeleting(id);
      setConfirmDelete(null);
      setConfirmRelease(null);
      try {
        await api.deleteVideo(id, releaseClips);
        await refetch();
      } catch (e: any) {
        setPublishError(e.message || t("videos:errors.delete"));
      } finally {
        setDeleting(null);
      }
    },
    [refetch, t]
  );

  const handleRegenerate = useCallback(
    async (id: number) => {
      setRegenerating(id);
      setConfirmRegenerate(null);
      try {
        await api.regenerateVideo(id);
        await refetch();
        setPlaying(null);
      } catch (e: any) {
        setPublishError(e.message || t("videos:errors.regenerate"));
      } finally {
        setRegenerating(null);
      }
    },
    [refetch, t]
  );

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{t("videos:title")}</h1>
          <p className="mt-1 text-sm text-text-secondary">
            {t("videos:subtitle")} {videos && `(${videos.length})`}
          </p>
        </div>
        {/* Search */}
        <div className="relative">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("videos:searchPlaceholder")}
            className="h-10 w-full sm:w-64 rounded-xl border border-border bg-surface px-10 text-sm text-text placeholder:text-text-muted transition-all focus:border-accent"
          />
        </div>
      </div>

      {/* Publish error toast */}
      {publishError && (
        <div className="flex items-center gap-3 rounded-xl border border-red-600/30 bg-red-600/10 px-4 py-3 text-sm text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{publishError}</span>
          <button onClick={() => setPublishError(null)} className="text-red-400/70 hover:text-red-400">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Grid */}
      {isLoading && !videos ? (
        <div className="flex justify-center py-32">
          <Spinner className="h-8 w-8" />
        </div>
      ) : !filtered || filtered.length === 0 ? (
        <Card>
          <EmptyState
            icon={<VideoIcon className="h-10 w-10" />}
            title={search ? t("videos:empty.searchTitle") : t("videos:empty.title")}
            description={
              search ? t("videos:empty.searchDescription") : t("videos:empty.description")
            }
          />
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {filtered.map((v: any) => {
            const statusCfg = VIDEO_STATUS_CONFIG[v.status] || VIDEO_STATUS_CONFIG.pending;
            const title = v.social_title || v.topic || "—";
            const isPublished = v.status === "published" && v.youtube_url;
            const canPublish =
              v.status === "pending_approval" || v.status === "publish_failed";

            return (
              <Card key={v.id} className="!p-0 overflow-hidden group flex flex-col">
                {/* Thumbnail */}
                <div
                  className="relative aspect-[9/16] bg-surface-elevated overflow-hidden cursor-pointer"
                  onClick={() => openModal(v)}
                >
                  {v.thumbnail_path ? (
                    <img
                      src={api.thumbUrl(v.id)}
                      alt={title}
                      className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
                    />
                  ) : (
                    <div className="flex h-full items-center justify-center text-text-muted">
                      <Film className="h-8 w-8" />
                    </div>
                  )}
                  {/* Play overlay */}
                  <div className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-all group-hover:bg-black/30 group-hover:opacity-100">
                    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent/90 backdrop-blur">
                      <Play className="h-5 w-5 text-white ml-0.5" fill="white" />
                    </div>
                  </div>
                  {/* QA badge */}
                  <div className="absolute bottom-2 right-2">
                    {v.qa_passed ? (
                      <Badge variant="success">
                        <CheckCircle2 className="h-3 w-3" /> QA {v.qa_score?.toFixed(0)}
                      </Badge>
                    ) : (
                      <Badge variant="error">
                        <XCircle className="h-3 w-3" /> QA {v.qa_score?.toFixed(0)}
                      </Badge>
                    )}
                  </div>
                  {/* YouTube badge */}
                  {isPublished && (
                    <div className="absolute top-2 left-2">
                      <Badge variant="info">
                        <Youtube className="h-3 w-3" /> YouTube
                      </Badge>
                    </div>
                  )}
                </div>

                {/* Info */}
                <div className="flex flex-1 flex-col p-3">
                  <p className="text-xs font-medium line-clamp-2 min-h-[2rem]" title={title}>
                    {title}
                  </p>
                  <div className="mt-2 flex items-center justify-between text-[10px] text-text-muted">
                    <span>{fmtDuration(v.duration)}</span>
                    <span>
                      {v.width}×{v.height}
                    </span>
                    <span>{fmtDate(v.created_at)}</span>
                  </div>

                  {/* Status + actions */}
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <Badge variant={statusCfg.variant}>{t(`videos:status.${v.status}`)}</Badge>

                    <div className="flex items-center gap-1.5">
                      {/* YouTube link */}
                      {isPublished && v.youtube_url && (
                        <a
                          href={v.youtube_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex h-7 w-7 items-center justify-center rounded-lg border border-border text-text-muted transition-all hover:border-red-600/40 hover:text-red-400"
                          title={t("videos:actions.openYoutube")}
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      )}
                      {/* Publish button */}
                      {canPublish && (
                        <button
                          onClick={() => handlePublish(v.id)}
                          disabled={publishing === v.id}
                          className="flex h-7 items-center gap-1.5 rounded-lg border border-border px-2.5 text-[11px] font-medium text-text-muted transition-all hover:border-accent/40 hover:text-accent disabled:opacity-50"
                          title={t("videos:actions.publishYoutube")}
                        >
                          {publishing === v.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Upload className="h-3 w-3" />
                          )}
                          {t("videos:actions.publish")}
                        </button>
                      )}
                      {/* Regenerate button */}
                      {v.knowledge_item && (
                        <button
                          onClick={() => setConfirmRegenerate(v)}
                          disabled={regenerating === v.id}
                          className="flex h-7 w-7 items-center justify-center rounded-lg border border-border text-text-muted transition-all hover:border-accent/40 hover:text-accent disabled:opacity-50"
                          title={t("videos:actions.regenerate")}
                        >
                          {regenerating === v.id ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <RotateCcw className="h-3.5 w-3.5" />
                          )}
                        </button>
                      )}
                      {/* Delete button */}
                      <button
                        onClick={() => setConfirmDelete(v.id)}
                        disabled={deleting === v.id}
                        className="flex h-7 w-7 items-center justify-center rounded-lg border border-border text-text-muted transition-all hover:border-red-600/40 hover:text-red-400 disabled:opacity-50"
                        title={t("videos:actions.delete")}
                      >
                        {deleting === v.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </div>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* Video player modal */}
      {playing && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-fade-in"
          onClick={() => setPlaying(null)}
        >
          <div
            className="relative w-full max-w-3xl max-h-[90vh] rounded-2xl border border-border bg-surface overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Close button */}
            <button
              onClick={() => setPlaying(null)}
              className="absolute right-3 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-lg bg-black/50 text-white/80 backdrop-blur transition-all hover:bg-black/70 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>

            {/* Video */}
            <video
              src={api.videoUrl(playing.id)}
              controls
              autoPlay
              className="w-full max-h-[45vh] bg-black shrink-0"
            />

            {/* Metadata — editable */}
            <div className="space-y-3 p-4 overflow-y-auto">
              {/* View mode */}
              {!editMode ? (
                <>
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="text-sm font-semibold flex-1">
                      {editTitle || t("videos:modal.videoNumber", { id: playing.id })}
                    </h3>
                    <button
                      onClick={() => setEditMode(true)}
                      className="shrink-0 rounded-lg border border-border px-2.5 py-1 text-[11px] font-medium text-text-muted transition-all hover:border-accent/40 hover:text-accent"
                    >
                      {t("videos:modal.edit")}
                    </button>
                  </div>
                  {editDescription && (
                    <p className="text-xs text-text-secondary line-clamp-4 whitespace-pre-wrap">
                      {editDescription}
                    </p>
                  )}
                  {editTags && (
                    <div className="flex flex-wrap gap-1.5">
                      {editTags.split(",").map((tag, i) => (
                        <span
                          key={i}
                          className="rounded-md bg-surface-elevated px-2 py-0.5 text-[10px] text-text-muted"
                        >
                          #{tag.trim().replace(/^#/, "")}
                        </span>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                /* Edit mode */
                <div className="space-y-3">
                  <div>
                    <label className="text-[10px] font-medium text-text-muted uppercase tracking-wide">
                      {t("videos:modal.titleLabel")}
                    </label>
                    <input
                      type="text"
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      maxLength={100}
                      className="mt-1 w-full rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:border-accent"
                      placeholder={t("videos:modal.titlePlaceholder")}
                    />
                    <p className="mt-1 text-[10px] text-text-muted text-right">
                      {editTitle.length}/100
                    </p>
                  </div>
                  <div>
                    <label className="text-[10px] font-medium text-text-muted uppercase tracking-wide">
                      {t("videos:modal.descriptionLabel")}
                    </label>
                    <textarea
                      value={editDescription}
                      onChange={(e) => setEditDescription(e.target.value)}
                      rows={4}
                      className="mt-1 w-full rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:border-accent resize-none"
                      placeholder={t("videos:modal.descriptionPlaceholder")}
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-medium text-text-muted uppercase tracking-wide">
                      {t("videos:modal.tagsLabel")}
                    </label>
                    <input
                      type="text"
                      value={editTags}
                      onChange={(e) => setEditTags(e.target.value)}
                      className="mt-1 w-full rounded-lg border border-border bg-surface-elevated px-3 py-2 text-sm text-text placeholder:text-text-muted focus:outline-none focus:border-accent"
                      placeholder={t("videos:modal.tagsPlaceholder")}
                    />
                  </div>
                  <div className="flex justify-end gap-2">
                    <button
                      onClick={() => setEditMode(false)}
                      className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text transition-colors"
                    >
                      {t("common:cancel")}
                    </button>
                    <button
                      onClick={() => handleSaveMetadata(playing.id)}
                      disabled={savingMeta}
                      className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/90 transition-colors disabled:opacity-50"
                    >
                      {savingMeta ? t("videos:modal.saving") : t("common:save")}
                    </button>
                  </div>
                </div>
              )}

              {/* Video info */}
              <div className="flex items-center gap-3 text-[10px] text-text-muted border-t border-border pt-3">
                <span>{fmtDuration(playing.duration)}</span>
                <span>
                  {playing.width}×{playing.height}
                </span>
                <span>{fmtDate(playing.created_at)}</span>
                {playing.youtube_url && (
                  <a
                    href={playing.youtube_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 text-red-400 hover:text-red-300"
                  >
                    <Youtube className="h-3 w-3" /> {t("videos:actions.openYoutube")}
                  </a>
                )}
              </div>

              {/* Rich metadata: idea, game, clips, script */}
              <div className="space-y-3 border-t border-border pt-3">
                {/* Idea (KnowledgeItem) */}
                {playing.knowledge_item && (
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5 text-[10px] font-medium text-text-muted uppercase tracking-wide">
                      <Lightbulb className="h-3 w-3" /> {t("videos:modal.idea")}
                    </div>
                    <p className="text-xs text-text-secondary">
                      {playing.knowledge_item.title}
                    </p>
                    {playing.knowledge_item.tags?.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {playing.knowledge_item.tags.map((tag: string, i: number) => (
                          <span
                            key={i}
                            className="rounded-md bg-surface-elevated px-1.5 py-0.5 text-[9px] text-text-muted"
                          >
                            #{tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {playing.knowledge_item.source_name && (
                      <p className="text-[10px] text-text-muted">
                        {t("videos:modal.source", { name: playing.knowledge_item.source_name })}
                      </p>
                    )}
                  </div>
                )}

                {/* Game */}
                {playing.game_name && (
                  <div className="space-y-1">
                    <div className="flex items-center gap-1.5 text-[10px] font-medium text-text-muted uppercase tracking-wide">
                      <Gamepad2 className="h-3 w-3" /> {t("videos:modal.game")}
                    </div>
                    <p className="text-xs text-text-secondary">{playing.game_name}</p>
                  </div>
                )}

                {/* Creative plan summary */}
                {playing.creative_plan && (
                  <div className="space-y-1">
                    <div className="flex items-center gap-1.5 text-[10px] font-medium text-text-muted uppercase tracking-wide">
                      <Sparkles className="h-3 w-3" /> {t("videos:modal.editorialPlan")}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <span className="rounded-md bg-surface-elevated px-2 py-0.5 text-[10px] text-text-muted">
                        {playing.creative_plan.video_type === "GENERAL_TOPIC" ? t("videos:modal.generalTopic") : t("videos:modal.aboutGame")}
                      </span>
                      {playing.creative_plan.humor_enabled !== null && (
                        <span className="rounded-md bg-surface-elevated px-2 py-0.5 text-[10px] text-text-muted">
                          {playing.creative_plan.humor_enabled ? t("videos:modal.humor") : t("videos:modal.noHumor")}
                          {playing.creative_plan.humor_enabled && playing.creative_plan.humor_intensity
                            ? ` (${playing.creative_plan.humor_intensity})`
                            : ""}
                        </span>
                      )}
                      {playing.creative_plan.narrative_beats > 0 && (
                        <span className="rounded-md bg-surface-elevated px-2 py-0.5 text-[10px] text-text-muted">
                          {playing.creative_plan.narrative_beats} beats
                        </span>
                      )}
                    </div>
                  </div>
                )}

                {/* Script review */}
                {playing.script_review && (
                  <div className="space-y-1">
                    <div className="flex items-center gap-1.5 text-[10px] font-medium text-text-muted uppercase tracking-wide">
                      <CheckCircle2 className="h-3 w-3" /> {t("videos:modal.scriptCritic")}
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant={playing.script_review.verdict === "PASS" ? "success" : "warning"}>
                        {playing.script_review.verdict}
                      </Badge>
                      {playing.script_review.score != null && (
                        <span className="text-[10px] text-text-muted">
                          {t("videos:modal.score", { score: playing.script_review.score.toFixed(0) })}
                        </span>
                      )}
                    </div>
                  </div>
                )}

                {/* Clips used */}
                {playing.clips_used?.length > 0 && (
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5 text-[10px] font-medium text-text-muted uppercase tracking-wide">
                      <Clapperboard className="h-3 w-3" /> {config.content.assetLabelPlural === "imagens"
                        ? t("videos:modal.clipsUsedImages", { count: playing.clips_used.length })
                        : t("videos:modal.clipsUsedGameplay", { count: playing.clips_used.length })}
                    </div>
                    <div className="space-y-1 max-h-32 overflow-y-auto">
                      {playing.clips_used.map((clip: any, i: number) => (
                        <div key={i} className="flex items-center justify-between text-[10px] text-text-muted bg-surface-elevated rounded-md px-2 py-1">
                          <span className="truncate flex-1">
                            {clip.source_game || clip.source_name || `Source #${clip.source_id}`}
                          </span>
                          <span className="shrink-0 ml-2 tabular-nums">
                            {clip.start_sec.toFixed(0)}s–{clip.end_sec.toFixed(0)}s
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Script */}
                {playing.script_final && (
                  <details className="space-y-1">
                    <summary className="flex cursor-pointer items-center gap-1.5 text-[10px] font-medium text-text-muted uppercase tracking-wide hover:text-text-secondary">
                      <FileText className="h-3 w-3" /> {t("videos:modal.script")}
                    </summary>
                    <p className="text-xs text-text-secondary whitespace-pre-wrap mt-1.5 p-2 bg-surface-elevated rounded-lg max-h-40 overflow-y-auto">
                      {playing.script_final}
                    </p>
                  </details>
                )}
              </div>

              {/* Regenerate button */}
              {playing.knowledge_item && (
                <button
                  onClick={() => setConfirmRegenerate(playing)}
                  disabled={regenerating === playing.id}
                  className="w-full flex items-center justify-center gap-2 rounded-xl border border-accent/30 bg-accent/10 px-4 py-2.5 text-sm font-medium text-accent transition-all hover:bg-accent/20 disabled:opacity-50"
                >
                  {regenerating === playing.id ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {t("videos:modal.regenerating")}
                    </>
                  ) : (
                    <>
                      <RotateCcw className="h-4 w-4" />
                      {t("videos:actions.regenerate")}
                    </>
                  )}
                </button>
              )}

              {/* Publish to YouTube button */}
              {canPublishModal(playing) && (
                <button
                  onClick={() => handlePublishFromModal(playing.id)}
                  disabled={publishing === playing.id}
                  className="w-full flex items-center justify-center gap-2 rounded-xl bg-red-600 px-4 py-3 text-sm font-semibold text-white transition-all hover:bg-red-700 disabled:opacity-50"
                >
                  {publishing === playing.id ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {t("videos:modal.publishing")}
                    </>
                  ) : (
                    <>
                      <Youtube className="h-4 w-4" />
                      {t("videos:actions.publishYoutube")}
                    </>
                  )}
                </button>
              )}
              {playing.status === "published" && (
                <div className="flex items-center justify-center gap-2 rounded-xl border border-green-600/30 bg-green-600/10 px-4 py-3 text-sm font-medium text-green-400">
                  <CheckCircle2 className="h-4 w-4" />
                  {t("videos:modal.published")}
                </div>
              )}
              {playing.status === "publish_failed" && (
                <div className="flex items-center justify-center gap-2 rounded-xl border border-red-600/30 bg-red-600/10 px-4 py-3 text-sm font-medium text-red-400">
                  <XCircle className="h-4 w-4" />
                  {t("videos:modal.publishFailed")}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Delete confirmation modal — step 1 */}
      {confirmDelete !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-fade-in"
          onClick={() => setConfirmDelete(null)}
        >
          <div
            className="relative w-full max-w-md rounded-2xl border border-border bg-surface p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-red-500/10 text-red-400">
                <Trash2 className="h-5 w-5" />
              </div>
              <h3 className="text-lg font-semibold">{t("videos:delete.title")}</h3>
            </div>
            <p className="text-sm text-text-secondary">
              {t("videos:delete.description")}
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setConfirmDelete(null)}
                className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text-primary transition-colors"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={() => setConfirmRelease(confirmDelete)}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 transition-colors"
              >
                {t("videos:delete.confirm")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirmation modal — step 2: release clips + idea? */}
      {confirmRelease !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-fade-in"
          onClick={() => setConfirmRelease(null)}
        >
          <div
            className="relative w-full max-w-md rounded-2xl border border-border bg-surface p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent/10 text-accent">
                <Film className="h-5 w-5" />
              </div>
              <h3 className="text-lg font-semibold">{t("videos:release.title")}</h3>
            </div>
            <p className="text-sm text-text-secondary">
              {t("videos:release.description", { sourceLabel: config.content.sourceLabelPlural })}
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setConfirmRelease(null)}
                className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text-primary transition-colors"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={() => handleDelete(confirmRelease, false)}
                className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text-primary transition-colors"
              >
                {t("videos:release.keepUsed")}
              </button>
              <button
                onClick={() => handleDelete(confirmRelease, true)}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/90 transition-colors"
              >
                {t("videos:release.confirm")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Regenerate confirmation modal */}
      {confirmRegenerate && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-fade-in"
          onClick={() => setConfirmRegenerate(null)}
        >
          <div
            className="relative w-full max-w-md rounded-2xl border border-border bg-surface p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent/10 text-accent">
                <RotateCcw className="h-5 w-5" />
              </div>
              <h3 className="text-lg font-semibold">{t("videos:regenerate.title")}</h3>
            </div>
            <div className="space-y-2">
              <p className="text-sm text-text-secondary">
                <Trans
                  t={t}
                  i18nKey="videos:regenerate.desc1"
                  components={[<strong key="0" />, <strong key="1" />]}
                  values={{ title: confirmRegenerate.knowledge_item?.title }}
                />
              </p>
              <p className="text-sm text-text-secondary">
                {t("videos:regenerate.desc2", { sourceLabel: config.content.sourceLabelPlural })}
              </p>
              <p className="text-sm text-text-secondary">
                <Trans
                  t={t}
                  i18nKey="videos:regenerate.desc3"
                  components={[<strong key="0" />]}
                />
              </p>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setConfirmRegenerate(null)}
                className="rounded-lg border border-border px-4 py-2 text-sm text-text-muted hover:text-text-primary transition-colors"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={() => handleRegenerate(confirmRegenerate.id)}
                disabled={regenerating === confirmRegenerate.id}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/90 transition-colors disabled:opacity-50"
              >
                {regenerating === confirmRegenerate.id ? t("videos:modal.regenerating") : t("videos:regenerate.confirm")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
