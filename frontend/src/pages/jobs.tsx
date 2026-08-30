import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { useLiveData } from "@/hooks/useLiveData";
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

const JOB_STATUS_CONFIG: Record<string, { variant: "default" | "success" | "warning" | "error" | "info"; icon: any }> = {
  queued: { variant: "info", icon: Clock },
  running: { variant: "info", icon: Loader2 },
  completed: { variant: "success", icon: CheckCircle2 },
  failed: { variant: "error", icon: XCircle },
  retrying: { variant: "warning", icon: AlertCircle },
  cancelled: { variant: "default", icon: XCircle },
};

export function JobsPage() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<string>("");
  const { data: jobs, isLoading } = useLiveData(['jobs'], () => api.listJobs(), ['job.status_changed', 'job.created']);

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
        <h1 className="text-3xl font-bold tracking-tight">{t('jobs:title')}</h1>
        <p className="mt-1 text-sm text-text-secondary">
          {t('jobs:subtitle')}
        </p>
      </div>

      {/* Filter tabs */}
      <div className="flex flex-wrap gap-2">
        <FilterTab active={filter === ""} onClick={() => setFilter("")} label={t('jobs:filter.all')} count={jobs?.length || 0} />
        <FilterTab active={filter === "queued"} onClick={() => setFilter("queued")} label={t('jobs:filter.queued')} count={counts.queued} />
        <FilterTab active={filter === "running"} onClick={() => setFilter("running")} label={t('jobs:filter.running')} count={counts.running} />
        <FilterTab active={filter === "completed"} onClick={() => setFilter("completed")} label={t('jobs:filter.completed')} count={counts.completed} />
        <FilterTab active={filter === "failed"} onClick={() => setFilter("failed")} label={t('jobs:filter.failed')} count={counts.failed} />
      </div>

      {/* Jobs list */}
      {isLoading && !jobs ? (
        <div className="flex justify-center py-16"><Spinner className="h-8 w-8" /></div>
      ) : !filtered || filtered.length === 0 ? (
        <Card>
          <EmptyState
            icon={<ListChecks className="h-10 w-10" />}
            title={t('jobs:empty.title')}
            description={t('jobs:empty.description')}
          />
        </Card>
      ) : (
        <div className="grid gap-3">
          {filtered.slice(0, 50).map((j: any) => {
            const cfg = JOB_STATUS_CONFIG[j.status] || JOB_STATUS_CONFIG.queued;
            const StatusIcon = cfg.icon;
            const isRunning = j.status === "running";
            const typeLabel = t(`jobs:type.${j.type}`, j.type) as string;
            const stageLabel = t(`stages:${j.stage}`, j.stage) as string;
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
                      {t(`jobs:status.${j.status}`, j.status) as string}
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
