import { useState } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { Badge, Card, Spinner, EmptyState } from "@/components/ui";
import { fmtDate, fmtDuration } from "@/lib/utils";
import { Video as VideoIcon, Play, Search, Film, CheckCircle2, XCircle } from "lucide-react";

export function VideosPage() {
  const { data: videos, loading } = usePoll(() => api.listVideos(), 10000);
  const [search, setSearch] = useState("");
  const [playing, setPlaying] = useState<number | null>(null);

  const filtered = videos?.filter((v: any) =>
    !search || (v.topic || "").toLowerCase().includes(search.toLowerCase())
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
            placeholder="Buscar por tópico..."
            className="h-10 w-full sm:w-64 rounded-xl border border-border bg-surface px-10 text-sm text-text placeholder:text-text-muted transition-all focus:border-accent"
          />
        </div>
      </div>

      {/* Grid */}
      {loading && !videos ? (
        <div className="flex justify-center py-32"><Spinner className="h-8 w-8" /></div>
      ) : !filtered || filtered.length === 0 ? (
        <Card>
          <EmptyState
            icon={<VideoIcon className="h-10 w-10" />}
            title={search ? "Nenhum vídeo encontrado" : "Nenhum vídeo gerado ainda"}
            description={search ? "Tente outra busca" : "Dispare uma geração na aba Automação para criar vídeos."}
          />
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {filtered.map((v: any) => (
            <Card key={v.id} className="!p-0 overflow-hidden group cursor-pointer" >
              {/* Thumbnail */}
              <div
                className="relative aspect-[9/16] bg-surface-elevated overflow-hidden"
                onClick={() => setPlaying(playing === v.id ? null : v.id)}
              >
                {v.thumbnail_path ? (
                  <img
                    src={api.thumbUrl(v.id)}
                    alt={v.topic || ""}
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
                    <Badge variant="success"><CheckCircle2 className="h-3 w-3" /> QA {v.qa_score?.toFixed(0)}</Badge>
                  ) : (
                    <Badge variant="error"><XCircle className="h-3 w-3" /> QA {v.qa_score?.toFixed(0)}</Badge>
                  )}
                </div>
                {/* Published badge */}
                {v.youtube_video_id && (
                  <div className="absolute top-2 left-2">
                    <Badge variant="info">YouTube</Badge>
                  </div>
                )}
              </div>

              {/* Info */}
              <div className="p-3">
                <p className="text-xs font-medium line-clamp-2 min-h-[2rem]">{v.topic || "—"}</p>
                <div className="mt-2 flex items-center justify-between text-[10px] text-text-muted">
                  <span>{fmtDuration(v.duration)}</span>
                  <span>{v.width}×{v.height}</span>
                  <span>{fmtDate(v.created_at)}</span>
                </div>
              </div>

              {/* Video player */}
              {playing === v.id && (
                <div className="border-t border-border p-2">
                  <video
                    src={api.videoUrl(v.id)}
                    controls
                    autoPlay
                    className="w-full rounded-lg"
                  />
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
