import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { Badge, Button, Card, Spinner, EmptyState } from "@/components/ui";
import { fmtDate, fmtDuration } from "@/lib/utils";
import { toast } from "sonner";
import {
  Youtube,
  Film,
  Loader2,
  Video as VideoIcon,
  Send,
  Play,
  Settings,
  FileText,
  CheckCircle2,
  XCircle,
  Clock,
} from "lucide-react";

export function DashboardPage() {
  const navigate = useNavigate();
  const { data: dash, loading } = usePoll(() => api.getDashboard(), 10000);
  const [triggering, setTriggering] = useState(false);

  const handleTrigger = async () => {
    setTriggering(true);
    try {
      await api.triggerRun();
      toast.success("Geração disparada! Acompanhe em Vídeos.");
    } catch (err: any) {
      toast.error(err.message || "Erro ao disparar geração");
    } finally {
      setTriggering(false);
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

  if (loading && !dash) {
    return (
      <div className="flex items-center justify-center py-32">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }

  const gameplays = dash?.gameplays || { total: 0, processing: 0, ready: 0 };
  const videos = dash?.videos || { total: 0, published: 0 };
  const jobs = dash?.jobs || { total: 0, running: 0 };
  const recentVideos = dash?.recent_videos || [];

  const stats = [
    { label: "Gameplays", value: gameplays.total, icon: Film, sub: `${gameplays.ready} prontos`, color: "text-accent" },
    { label: "Processando", value: gameplays.processing, icon: Loader2, sub: gameplays.processing > 0 ? "em análise" : "tudo ok", color: "text-accent-warm" },
    { label: "Vídeos gerados", value: videos.total, icon: VideoIcon, sub: `${jobs.running} rodando`, color: "text-accent" },
    { label: "Publicados", value: videos.published, icon: Send, sub: "no YouTube", color: "text-accent-warm" },
  ];

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-text-secondary">Visão geral da sua automação de conteúdo</p>
        </div>
        <Button variant="primary" size="lg" onClick={handleTrigger} disabled={triggering}>
          {triggering ? (
            <><span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" /> Disparando...</>
          ) : (
            <><Play className="h-4 w-4" /> Gerar agora</>
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

      <div className="grid gap-6 lg:grid-cols-3">
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

        {/* Automation status */}
        <Card className="lg:col-span-1">
          <div className="flex items-center gap-2 mb-4">
            <Settings className="h-5 w-5 text-accent" />
            <h2 className="text-sm font-semibold">Automação</h2>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">Status</span>
              <Badge variant={dash?.automation_status === "running" ? "success" : "default"}>
                {dash?.automation_status || "idle"}
              </Badge>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">Jobs ativos</span>
              <span className="text-sm font-medium">{jobs.running}</span>
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
              <span>Configurar geração</span>
            </button>
            <button
              onClick={() => navigate("/videos")}
              className="flex w-full items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2.5 text-sm transition-all hover:border-border-bright hover:bg-surface-hover"
            >
              <VideoIcon className="h-4 w-4 text-accent" />
              <span>Ver vídeos</span>
            </button>
          </div>
        </Card>
      </div>

      {/* Recent videos */}
      <div>
        <h2 className="mb-4 text-lg font-semibold">Vídeos recentes</h2>
        {recentVideos.length === 0 ? (
          <Card>
            <EmptyState
              icon={<VideoIcon className="h-10 w-10" />}
              title="Nenhum vídeo ainda"
              description="Dispare uma geração para começar a produzir conteúdo."
              action={<Button variant="primary" onClick={handleTrigger} disabled={triggering}><Play className="h-4 w-4" /> Gerar agora</Button>}
            />
          </Card>
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
            {recentVideos.slice(0, 5).map((v: any) => (
              <Card key={v.id} className="!p-0 overflow-hidden group cursor-pointer" >
                <div className="relative aspect-[9/16] bg-surface-elevated overflow-hidden">
                  {v.thumbnail_path ? (
                    <img src={api.thumbUrl(v.id)} alt={v.topic || ""} className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105" />
                  ) : (
                    <div className="flex h-full items-center justify-center text-text-muted">
                      <VideoIcon className="h-8 w-8" />
                    </div>
                  )}
                  <div className="absolute bottom-2 right-2">
                    {v.qa_passed ? (
                      <Badge variant="success">QA {v.qa_score?.toFixed(0)}</Badge>
                    ) : (
                      <Badge variant="error">QA {v.qa_score?.toFixed(0)}</Badge>
                    )}
                  </div>
                </div>
                <div className="p-3">
                  <p className="text-xs font-medium truncate">{v.topic || "—"}</p>
                  <div className="mt-1 flex items-center justify-between text-[10px] text-text-muted">
                    <span>{fmtDuration(v.duration)}</span>
                    <span>{fmtDate(v.created_at)}</span>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
