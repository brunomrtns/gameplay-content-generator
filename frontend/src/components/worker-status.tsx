import { usePoll } from "@/hooks/usePoll";
import { api } from "@/lib/api";
import { Card, Badge } from "@/components/ui";
import { Cpu, HardDrive, Activity, Monitor, Server, CircleDot } from "lucide-react";

export function WorkerStatusCard() {
  const { data } = usePoll(() => api.listWorkers(), 5000);
  const workers = data?.workers || [];

  if (workers.length === 0) {
    return (
      <Card className="!p-5">
        <div className="flex items-center gap-2 mb-3">
          <Server className="h-5 w-5 text-text-muted" />
          <h2 className="text-sm font-semibold">Workers</h2>
        </div>
        <div className="flex items-center gap-2 text-sm text-text-muted">
          <CircleDot className="h-4 w-4" />
          <span>Nenhum worker registrado</span>
        </div>
        <p className="mt-2 text-xs text-text-muted">
          Inicie um worker local com <code className="px-1 py-0.5 rounded bg-surface-elevated text-accent">gpcg remote-worker</code> para processar gameplays e gerar vídeos.
        </p>
      </Card>
    );
  }

  return (
    <Card className="!p-5">
      <div className="flex items-center gap-2 mb-4">
        <Server className="h-5 w-5 text-accent" />
        <h2 className="text-sm font-semibold">Workers</h2>
        <Badge variant="default" className="ml-auto">{workers.length}</Badge>
      </div>
      <div className="space-y-4">
        {workers.map((w: any) => {
          const isOnline = w.status === "online" || w.status === "busy";
          const isBusy = w.status === "busy";
          return (
            <div key={w.worker_id} className="space-y-2">
              {/* Worker header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${isOnline ? "bg-accent animate-pulse" : "bg-red-500"}`} />
                  <span className="text-sm font-medium">{w.worker_id}</span>
                  {w.gpu_name && (
                    <span className="text-xs text-text-muted">({w.gpu_name})</span>
                  )}
                </div>
                <Badge variant={isOnline ? (isBusy ? "success" : "default") : "error"}>
                  {isBusy ? "Processando" : isOnline ? "Online" : "Offline"}
                </Badge>
              </div>

              {/* Current activity */}
              {w.current_activity && (
                <div className="flex items-center gap-2 text-xs text-text-secondary">
                  <Activity className="h-3.5 w-3.5 text-accent-warm" />
                  <span>{w.current_activity}</span>
                </div>
              )}

              {/* Hardware stats */}
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div className="flex items-center gap-1.5">
                  <Monitor className="h-3.5 w-3.5 text-text-muted" />
                  <span className="text-text-muted">GPU</span>
                  <span className="font-medium">{w.gpu_usage != null ? `${w.gpu_usage.toFixed(0)}%` : "—"}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Cpu className="h-3.5 w-3.5 text-text-muted" />
                  <span className="text-text-muted">CPU</span>
                  <span className="font-medium">{w.cpu_usage != null ? `${w.cpu_usage.toFixed(0)}%` : "—"}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <HardDrive className="h-3.5 w-3.5 text-text-muted" />
                  <span className="text-text-muted">RAM</span>
                  <span className="font-medium">{w.ram_usage != null ? `${w.ram_usage.toFixed(1)}GB` : "—"}</span>
                </div>
              </div>

              {/* Capabilities */}
              {w.capabilities && w.capabilities.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {w.capabilities.map((cap: string) => (
                    <span key={cap} className="px-1.5 py-0.5 text-[10px] rounded bg-surface-elevated border border-border text-text-muted">
                      {cap}
                    </span>
                  ))}
                </div>
              )}

              {/* Last heartbeat */}
              {w.last_heartbeat && (
                <div className="text-[10px] text-text-muted">
                  Último heartbeat: {timeAgo(w.last_heartbeat)}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 5) return "agora mesmo";
  if (sec < 60) return `${sec}s atrás`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}min atrás`;
  const hr = Math.floor(min / 60);
  return `${hr}h atrás`;
}
