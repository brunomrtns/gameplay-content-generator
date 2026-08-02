import { useState, useCallback } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
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
} from "lucide-react";

const VIDEO_STATUS_CONFIG: Record<
  string,
  { variant: "default" | "success" | "warning" | "error" | "info"; label: string }
> = {
  pending: { variant: "default", label: "Pendente" },
  ready: { variant: "info", label: "Pronto" },
  qa_passed: { variant: "success", label: "QA OK" },
  qa_failed: { variant: "error", label: "QA Falhou" },
  pending_approval: { variant: "warning", label: "Aguardando publicação" },
  published: { variant: "success", label: "Publicado" },
  publish_failed: { variant: "error", label: "Publicação falhou" },
};

export function VideosPage() {
  const { data: videos, loading, refetch } = usePoll(() => api.listVideos(), 10000);
  const [search, setSearch] = useState("");
  const [playing, setPlaying] = useState<any | null>(null);
  const [publishing, setPublishing] = useState<number | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);

  const filtered = videos?.filter((v: any) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      (v.social_title || "").toLowerCase().includes(q) ||
      (v.topic || "").toLowerCase().includes(q)
    );
  });

  const handlePublish = useCallback(
    async (id: number) => {
      setPublishing(id);
      setPublishError(null);
      try {
        await api.publishVideo(id);
        await refetch();
      } catch (e: any) {
        setPublishError(e.message || "Falha ao publicar no YouTube");
      } finally {
        setPublishing(null);
      }
    },
    [refetch]
  );

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Vídeos</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Galeria de vídeos gerados {videos && `(${videos.length})`}
          </p>
        </div>
        {/* Search */}
        <div className="relative">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar por título..."
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
      {loading && !videos ? (
        <div className="flex justify-center py-32">
          <Spinner className="h-8 w-8" />
        </div>
      ) : !filtered || filtered.length === 0 ? (
        <Card>
          <EmptyState
            icon={<VideoIcon className="h-10 w-10" />}
            title={search ? "Nenhum vídeo encontrado" : "Nenhum vídeo gerado ainda"}
            description={
              search ? "Tente outra busca" : "Dispare uma geração na aba Automação para criar vídeos."
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
                  onClick={() => setPlaying(v)}
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
                    <Badge variant={statusCfg.variant}>{statusCfg.label}</Badge>

                    <div className="flex items-center gap-1.5">
                      {/* YouTube link */}
                      {isPublished && (
                        <a
                          href={v.youtube_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex h-7 w-7 items-center justify-center rounded-lg border border-border text-text-muted transition-all hover:border-red-600/40 hover:text-red-400"
                          title="Abrir no YouTube"
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
                          title="Publicar no YouTube"
                        >
                          {publishing === v.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Upload className="h-3 w-3" />
                          )}
                          Publicar
                        </button>
                      )}
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
            className="relative w-full max-w-3xl rounded-2xl border border-border bg-surface overflow-hidden"
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
              className="w-full max-h-[70vh] bg-black"
            />

            {/* Metadata */}
            <div className="space-y-3 p-4">
              <h3 className="text-sm font-semibold">
                {playing.social_title || playing.topic || `Vídeo #${playing.id}`}
              </h3>
              {playing.social_description && (
                <p className="text-xs text-text-secondary line-clamp-3 whitespace-pre-wrap">
                  {playing.social_description}
                </p>
              )}
              {playing.social_tags && playing.social_tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {playing.social_tags.map((tag: string, i: number) => (
                    <span
                      key={i}
                      className="rounded-md bg-surface-elevated px-2 py-0.5 text-[10px] text-text-muted"
                    >
                      #{tag}
                    </span>
                  ))}
                </div>
              )}
              <div className="flex items-center gap-3 text-[10px] text-text-muted">
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
                    <Youtube className="h-3 w-3" /> Abrir no YouTube
                  </a>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
