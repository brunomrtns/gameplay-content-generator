import { useState } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { Badge, Card, Spinner, EmptyState } from "@/components/ui";
import { fmtDate } from "@/lib/utils";
import {
  ListChecks,
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  Cpu,
  Video as VideoIcon,
  AlertCircle,
} from "lucide-react";

const JOB_STATUS_CONFIG: Record<string, { variant: "default" | "success" | "warning" | "error" | "info"; label: string; icon: any }> = {
  queued: { variant: "info", label: "Na fila", icon: Clock },
  running: { variant: "info", label: "Executando", icon: Loader2 },
  completed: { variant: "success", label: "Concluído", icon: CheckCircle2 },
  failed: { variant: "error", label: "Falhou", icon: XCircle },
  retrying: { variant: "warning", label: "Tentando novamente", icon: AlertCircle },
  cancelled: { variant: "default", label: "Cancelado", icon: XCircle },
};

const JOB_TYPE_LABELS: Record<string, string> = {
  mapping: "Mapeamento",
  generate_short: "Gerar Short",
  curiosity_short: "Curiosidade",
};

const STAGE_LABELS: Record<string, string> = {
  download: "Download",
  confirm_download: "Confirmando download",
  mapping: "Mapeando (VLM + ASR)",
  content_planning: "Planejando conteúdo",
  editorial_planning: "Planejamento editorial",
  creative_engine: "Motor criativo",
  script: "Escrevendo roteiro",
  script_review: "Revisando roteiro",
  tts: "Sintetizando voz",
  gameplay_selection: "Selecionando cenas",
  visual_selection: "Selecionando imagens",
  music_selection: "Selecionando música",
  render_plan: "Planejando renderização",
  render: "Renderizando vídeo",
  qa: "Controle de qualidade",
  metadata_generation: "Gerando metadados",
  youtube_upload: "Enviando ao YouTube",
  output: "Finalizando",
  done: "Concluído",
};

export function JobsPage() {
  const [filter, setFilter] = useState<string>("");
  const { data: jobs, loading } = usePoll(() => api.listJobs(), 5000);

  const filtered = filter
    ? (jobs || []).filter((j: any) => j.status === filter)
    : jobs || [];

  const counts = {
    queued: (jobs || []).filter((j: any) => j.status === "queued").length,
    running: (jobs || []).filter((j: any) => j.status === "running").length,
    completed: (jobs || []).filter((j: any) => j.status === "completed").length,
    failed: (jobs || []).filter((j: any) => j.status === "failed").length,
  };

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Jobs</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Fila de processamento — mapeamentos e geração de vídeos
        </p>
      </div>

      {/* Filter tabs */}
      <div className="flex flex-wrap gap-2">
        <FilterTab active={filter === ""} onClick={() => setFilter("")} label="Todos" count={jobs?.length || 0} />
        <FilterTab active={filter === "queued"} onClick={() => setFilter("queued")} label="Na fila" count={counts.queued} />
        <FilterTab active={filter === "running"} onClick={() => setFilter("running")} label="Executando" count={counts.running} />
        <FilterTab active={filter === "completed"} onClick={() => setFilter("completed")} label="Concluídos" count={counts.completed} />
        <FilterTab active={filter === "failed"} onClick={() => setFilter("failed")} label="Falhas" count={counts.failed} />
      </div>

      {/* Jobs list */}
      {loading && !jobs ? (
        <div className="flex justify-center py-16"><Spinner className="h-8 w-8" /></div>
      ) : !filtered || filtered.length === 0 ? (
        <Card>
          <EmptyState
            icon={<ListChecks className="h-10 w-10" />}
            title="Nenhum job"
            description="Solicite mapeamentos na aba Conteúdo ou inicie a automação para gerar vídeos."
          />
        </Card>
      ) : (
        <div className="grid gap-3">
          {filtered.slice(0, 50).map((j: any) => {
            const cfg = JOB_STATUS_CONFIG[j.status] || JOB_STATUS_CONFIG.queued;
            const StatusIcon = cfg.icon;
            const isRunning = j.status === "running";
            const typeLabel = JOB_TYPE_LABELS[j.type] || j.type;
            const stageLabel = STAGE_LABELS[j.stage] || j.stage;
            const progress = j.progress != null ? Math.round(j.progress * 100) : 0;

            return (
              <Card key={j.id} className="!p-4">
                <div className="flex items-start gap-4">
                  {/* Icon */}
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-surface-elevated border border-border">
                    {j.type === "mapping" ? (
                      <Cpu className="h-5 w-5 text-accent" />
                    ) : (
                      <VideoIcon className="h-5 w-5 text-accent" />
                    )}
                  </div>

                  {/* Info */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-text-muted">#{j.id}</span>
                      <span className="text-sm font-medium">{typeLabel}</span>
                      {j.game_name && (
                        <span className="text-xs text-text-secondary">— {j.game_name}</span>
                      )}
                    </div>

                    {/* Stage + progress */}
                    {isRunning && (
                      <div className="mt-2 space-y-1.5">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-text-secondary">{stageLabel}</span>
                          <span className="font-medium text-accent">{progress}%</span>
                        </div>
                        <div className="h-1.5 overflow-hidden rounded-full bg-surface-elevated">
                          <div
                            className="h-full rounded-full bg-accent transition-all duration-500"
                            style={{ width: `${progress}%` }}
                          />
                        </div>
                      </div>
                    )}

                    {/* Worker assignment */}
                    {j.worker_id && (
                      <div className="mt-1.5 flex items-center gap-1.5 text-xs text-text-muted">
                        <Cpu className="h-3 w-3" />
                        <span>Worker: {j.worker_id}</span>
                      </div>
                    )}

                    {/* Error */}
                    {j.status === "failed" && j.error && (
                      <p className="mt-2 text-xs text-red-400 line-clamp-2">{j.error}</p>
                    )}

                    {/* Timestamps */}
                    <div className="mt-2 flex items-center gap-3 text-[10px] text-text-muted">
                      <span>Criado: {fmtDate(j.created_at)}</span>
                      {j.completed_at && <span>Concluído: {fmtDate(j.completed_at)}</span>}
                      {j.attempts > 1 && <span>Tentativas: {j.attempts}</span>}
                    </div>
                  </div>

                  {/* Status badge */}
                  <div className="shrink-0">
                    <Badge variant={cfg.variant}>
                      <StatusIcon className={`h-3 w-3 ${isRunning ? "animate-spin" : ""}`} />
                      {cfg.label}
                    </Badge>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

function FilterTab({ active, onClick, label, count }: { active: boolean; onClick: () => void; label: string; count: number }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-all ${
        active
          ? "bg-accent/10 text-accent border border-accent/30"
          : "text-text-secondary hover:text-text hover:bg-surface-hover border border-transparent"
      }`}
    >
      {label}
      <span className={`rounded px-1.5 py-0.5 text-[10px] ${active ? "bg-accent/20 text-accent" : "bg-surface-elevated text-text-muted"}`}>
        {count}
      </span>
    </button>
  );
}
