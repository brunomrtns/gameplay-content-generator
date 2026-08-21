import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { Badge, Button, Card, Spinner, EmptyState } from "@/components/ui";
import { WorkerStatusCard } from "@/components/worker-status";
import { fmtDate, fmtDuration } from "@/lib/utils";
import { toast } from "sonner";
import {
  Youtube,
  Film,
  Loader2,
  Video as VideoIcon,
  Send,
  Play,
  Pause,
  Settings,
  FileText,
  CheckCircle2,
  XCircle,
  Clock,
  Zap,
  Upload,
  X,
  ExternalLink,
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

export function DashboardPage() {
  const navigate = useNavigate();
  const { data: dash, loading, refetch } = usePoll(() => api.getDashboard(), 10000);
  const [toggling, setToggling] = useState(false);
  const [playing, setPlaying] = useState<any | null>(null);
  const [publishing, setPublishing] = useState<number | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);

  const automationRunning = dash?.automation_status === "running";

  const handleToggleAutomation = async () => {
    setToggling(true);
    try {
      if (automationRunning) {
        await api.pauseAutomation();
        toast.success("Automação pausada. O vídeo atual será concluído.");
      } else {
        await api.startAutomation();
        toast.success("Automação iniciada! Vídeos serão produzidos continuamente.");
      }
    } catch (err: any) {
      toast.error(err.message || "Erro ao alterar automação");
    } finally {
      setToggling(false);
    }
  };

  const handleYouTubeConnect = async () => {
    try {
      const { url } = await api.youtubeConnect();
      window.location.href = url;
    } catch (err: any) {
      toast.error(err.message);
    }
  };

  const handlePublish = useCallback(
    async (id: number) => {
      setPublishing(id);
      setPublishError(null);
      try {
        await api.publishVideo(id);
        await refetch();
        toast.success("Vídeo publicado no YouTube!");
      } catch (e: any) {
        setPublishError(e.message || "Falha ao publicar no YouTube");
        toast.error(e.message || "Falha ao publicar");
      } finally {
        setPublishing(null);
      }
    },
    [refetch]
  );

  if (loading && !dash) {
    return (
      <div className="flex items-center justify-center py-32">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }

  const gameplays = dash?.gameplays || { total: 0, processing: 0, ready: 0 };
  const kids = dash?.kids || { total_topics: 0, total_assets: 0, ready_assets: 0 };
  const videos = dash?.videos || { total: 0, published: 0 };
  const jobs = dash?.jobs || { total: 0, running: 0 };
  const recentVideos = dash?.recent_videos || [];
  const isKidsDomain = dash?.channel_domain === "kids";

  const stats = isKidsDomain
    ? [
        { label: "Tópicos", value: kids.total_topics, icon: FileText, sub: `${kids.ready_assets} imagens prontas`, color: "text-accent" },
        { label: "Imagens", value: kids.total_assets, icon: Upload, sub: `${kids.ready_assets} prontas`, color: "text-accent-warm" },
        { label: "Vídeos produzidos", value: videos.total, icon: VideoIcon, sub: jobs.running > 0 ? "produzindo agora" : "em pausa", color: "text-accent" },
        { label: "Publicados", value: videos.published, icon: Send, sub: "no YouTube", color: "text-accent-warm" },
      ]
    : [
        { label: "Gameplays", value: gameplays.total, icon: Film, sub: `${gameplays.ready} prontos`, color: "text-accent" },
        { label: "Processando", value: gameplays.processing, icon: Loader2, sub: gameplays.processing > 0 ? "em análise" : "tudo ok", color: "text-accent-warm" },
        { label: "Vídeos produzidos", value: videos.total, icon: VideoIcon, sub: jobs.running > 0 ? "produzindo agora" : "em pausa", color: "text-accent" },
        { label: "Publicados", value: videos.published, icon: Send, sub: "no YouTube", color: "text-accent-warm" },
      ];

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header com controle da automação */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-text-secondary">Sua máquina de produção de conteúdo</p>
          {dash?.channel_domain && (
            <span className="mt-1 inline-flex items-center gap-1.5 rounded-md bg-accent/10 border border-accent/20 px-2 py-0.5 text-[10px] font-medium text-accent">
              {dash.channel_domain === "games" ? "Games" : dash.channel_domain === "kids" ? "Kids" : dash.channel_domain}
            </span>
          )}
        </div>
        <Button
          variant={automationRunning ? "danger" : "primary"}
          size="lg"
          onClick={handleToggleAutomation}
          disabled={toggling}
        >
          {toggling ? (
            <><span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" /> Aguarde...</>
          ) : automationRunning ? (
            <><Pause className="h-4 w-4" /> Pausar automação</>
          ) : (
            <><Play className="h-4 w-4" /> Iniciar automação</>
          )}
        </Button>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((s) => (
          <Card key={s.label} className="!p-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs font-medium uppercase tracking-wider text-text-muted">{s.label}</p>
                <p className="mt-2 text-3xl font-bold tracking-tight">{s.value}</p>
                <p className={`mt-1 text-xs ${s.color}`}>{s.sub}</p>
              </div>
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-surface-elevated border border-border">
                <s.icon className={`h-5 w-5 ${s.color}`} />
              </div>
            </div>
          </Card>
        ))}
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

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Worker status card */}
        <WorkerStatusCard />

        {/* YouTube card */}
        <Card className="lg:col-span-1">
          <div className="flex items-center gap-2 mb-4">
            <Youtube className={`h-5 w-5 ${dash?.youtube_connected ? "text-red-500" : "text-text-muted"}`} />
            <h2 className="text-sm font-semibold">YouTube</h2>
          </div>
          {dash?.youtube_connected ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-accent" />
                <span className="text-sm font-medium">{dash?.youtube_channel || "Conectado"}</span>
              </div>
              <Badge variant="success">Conectado</Badge>
              <p className="text-xs text-text-muted">Vídeos serão publicados automaticamente conforme a automação.</p>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <XCircle className="h-4 w-4 text-text-muted" />
                <span className="text-sm text-text-secondary">Não conectado</span>
              </div>
              <Button variant="outline" size="sm" onClick={handleYouTubeConnect} className="w-full">
                <Youtube className="h-4 w-4" /> Conectar YouTube
              </Button>
            </div>
          )}
        </Card>

        {/* Automation status — destaque visual */}
        <Card className="lg:col-span-1">
          <div className="flex items-center gap-2 mb-4">
            <Zap className={`h-5 w-5 ${automationRunning ? "text-accent" : "text-text-muted"}`} />
            <h2 className="text-sm font-semibold">Automação</h2>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">Status</span>
              <Badge variant={automationRunning ? "success" : "default"}>
                {automationRunning ? (
                  <><span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" /> Produzindo</>
                ) : (
                  dash?.automation_status === "paused" ? "Pausada" : "Parada"
                )}
              </Badge>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">Sendo produzido</span>
              <span className="text-sm font-medium">{jobs.running} {jobs.running === 1 ? "vídeo" : "vídeos"}</span>
            </div>
            <Button variant="ghost" size="sm" className="w-full" onClick={() => navigate("/automation")}>
              Configurar automação
            </Button>
          </div>
        </Card>

        {/* Quick actions */}
        <Card className="lg:col-span-1">
          <div className="flex items-center gap-2 mb-4">
            <Clock className="h-5 w-5 text-accent-warm" />
            <h2 className="text-sm font-semibold">Atalhos</h2>
          </div>
          <div className="space-y-2">
            <button
              onClick={() => navigate("/content")}
              className="flex w-full items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2.5 text-sm transition-all hover:border-border-bright hover:bg-surface-hover"
            >
              <FileText className="h-4 w-4 text-accent" />
              <span>Enviar gameplays</span>
            </button>
            <button
              onClick={() => navigate("/automation")}
              className="flex w-full items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2.5 text-sm transition-all hover:border-border-bright hover:bg-surface-hover"
            >
              <Settings className="h-4 w-4 text-accent" />
              <span>Configurar produção</span>
            </button>
            <button
              onClick={() => navigate("/videos")}
              className="flex w-full items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2.5 text-sm transition-all hover:border-border-bright hover:bg-surface-hover"
            >
              <VideoIcon className="h-4 w-4 text-accent" />
              <span>Ver vídeos produzidos</span>
            </button>
          </div>
        </Card>
      </div>

      {/* Recent videos with actions */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Vídeos produzidos</h2>
          {recentVideos.length > 0 && (
            <button
              onClick={() => navigate("/videos")}
              className="text-xs text-text-muted hover:text-accent transition-colors"
            >
              Ver todos →
            </button>
          )}
        </div>
        {recentVideos.length === 0 ? (
          <Card>
            <EmptyState
              icon={<VideoIcon className="h-10 w-10" />}
              title="Nenhum vídeo produzido ainda"
              description="Inicie a automação para começar a produzir conteúdo continuamente."
              action={
                <Button variant="primary" onClick={handleToggleAutomation} disabled={toggling}>
                  <Play className="h-4 w-4" /> Iniciar automação
                </Button>
              }
            />
          </Card>
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
            {recentVideos.slice(0, 5).map((v: any) => {
              const title = v.social_title || v.topic || "—";
              const isPublished = v.status === "published" && v.youtube_url;
              const canPublish = v.status === "pending_approval" || v.status === "publish_failed";
              const statusCfg = VIDEO_STATUS_CONFIG[v.status] || VIDEO_STATUS_CONFIG.pending;
              return (
                <Card key={v.id} className="!p-0 overflow-hidden group flex flex-col">
                  {/* Thumbnail */}
                  <div
                    className="relative aspect-[9/16] bg-surface-elevated overflow-hidden cursor-pointer"
                    onClick={() => setPlaying(v)}
                  >
                    {v.thumbnail_path ? (
                      <img src={api.thumbUrl(v.id)} alt={title} className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105" />
                    ) : (
                      <div className="flex h-full items-center justify-center text-text-muted">
                        <VideoIcon className="h-8 w-8" />
                      </div>
                    )}
                    {/* Play overlay */}
                    <div className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-all group-hover:bg-black/30 group-hover:opacity-100">
                      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent/90 backdrop-blur">
                        <Play className="h-5 w-5 text-white ml-0.5" fill="white" />
                      </div>
                    </div>
                    <div className="absolute bottom-2 right-2">
                      {v.qa_passed ? (
                        <Badge variant="success">QA {v.qa_score?.toFixed(0)}</Badge>
                      ) : (
                        <Badge variant="error">QA {v.qa_score?.toFixed(0)}</Badge>
                      )}
                    </div>
                    {isPublished && (
                      <div className="absolute top-2 left-2">
                        <Badge variant="info">YouTube</Badge>
                      </div>
                    )}
                  </div>
                  {/* Info + actions */}
                  <div className="flex flex-1 flex-col p-3">
                    <p className="text-xs font-medium line-clamp-2 min-h-[2rem]" title={title}>{title}</p>
                    <div className="mt-1 flex items-center justify-between text-[10px] text-text-muted">
                      <span>{fmtDuration(v.duration)}</span>
                      <span>{fmtDate(v.created_at)}</span>
                    </div>
                    {/* Actions */}
                    <div className="mt-2 flex items-center justify-between gap-1.5">
                      <Badge variant={statusCfg.variant}>{statusCfg.label}</Badge>
                      <div className="flex items-center gap-1">
                        {isPublished && (
                          <a
                            href={v.youtube_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex h-6 w-6 items-center justify-center rounded-lg border border-border text-text-muted transition-all hover:border-red-600/40 hover:text-red-400"
                            title="Abrir no YouTube"
                          >
                            <ExternalLink className="h-3 w-3" />
                          </a>
                        )}
                        {canPublish && (
                          <button
                            onClick={() => handlePublish(v.id)}
                            disabled={publishing === v.id}
                            className="flex h-6 items-center gap-1 rounded-lg border border-border px-2 text-[10px] font-medium text-text-muted transition-all hover:border-accent/40 hover:text-accent disabled:opacity-50"
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
      </div>

      {/* Video player modal (reused from videos.tsx) */}
      {playing && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-fade-in"
          onClick={() => setPlaying(null)}
        >
          <div
            className="relative w-full max-w-3xl rounded-2xl border border-border bg-surface overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setPlaying(null)}
              className="absolute right-3 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-lg bg-black/50 text-white/80 backdrop-blur transition-all hover:bg-black/70 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
            <video
              src={api.videoUrl(playing.id)}
              controls
              autoPlay
              className="w-full max-h-[70vh] bg-black"
            />
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
                {playing.width && playing.height && <span>{playing.width}×{playing.height}</span>}
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
