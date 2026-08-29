import { useLiveData } from "@/hooks/useLiveData";
import { api } from "@/lib/api";
import { Card, Badge } from "@/components/ui";
import { Cpu, HardDrive, Activity, Monitor, Server, CircleDot, AlertTriangle, Clock } from "lucide-react";

export function WorkerStatusCard() {
  const { data } = useLiveData(['workers'], () => api.listWorkers(), ['worker.status_changed']);
  const workers = data?.workers || [];

  // V3: Group workers by hostname to show machine/process distinction
  // Workers with the same hostname = same physical machine (possibly multiple processes)
  // Workers with different hostnames = different machines
  const byHostname = workers.reduce((acc: Record<string, any[]>, w: any) => {
    const h = w.hostname || "unknown";
    if (!acc[h]) acc[h] = [];
    acc[h].push(w);
    return acc;
  }, {});

  const activeCount = workers.filter((w: any) => w.status === "online" || w.status === "busy").length;
  const staleCount = workers.filter((w: any) => w.stale).length;

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
        <div className="ml-auto flex items-center gap-2">
          {activeCount > 0 && <Badge variant="success">{activeCount} ativo{activeCount > 1 ? "s" : ""}</Badge>}
          {staleCount > 0 && <Badge variant="warning">{staleCount} obsoleto{staleCount > 1 ? "s" : ""}</Badge>}
          <Badge variant="default">{workers.length} total</Badge>
        </div>
      </div>

      <div className="space-y-5">
        {Object.entries(byHostname).map(([hostname, hostWorkers]: [string, any[]]) => (
          <div key={hostname} className="space-y-3">
            {/* Machine header */}
            <div className="flex items-center gap-2 text-xs text-text-muted border-b border-border pb-1">
              <Monitor className="h-3.5 w-3.5" />
              <span className="font-medium uppercase tracking-wider">{hostname}</span>
              <span className="text-text-muted/60">·</span>
              <span>{hostWorkers.length} processo{hostWorkers.length > 1 ? "s" : ""}</span>
            </div>

            {/* Workers on this machine */}
            {hostWorkers.map((w: any) => (
              <WorkerRow key={w.worker_id} w={w} />
            ))}
          </div>
        ))}
      </div>
    </Card>
  );
}

function WorkerRow({ w }: { w: any }) {
  const isOnline = w.status === "online" || w.status === "busy";
  const isBusy = w.status === "busy";
  const isStale = w.stale;

  // V3: Status display — active/offline/stale with clear distinction
  const statusLabel = isStale
    ? "Obsoleto"
    : isBusy
    ? "Processando"
    : isOnline
    ? "Online"
    : "Offline";

  const statusVariant = isStale
    ? "warning"
    : isOnline
    ? (isBusy ? "success" : "default")
    : "error";

  return (
    <div className={`space-y-2 ${isStale ? "opacity-60" : ""}`}>
      {/* Worker header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${
            isStale ? "bg-yellow-500" : isOnline ? "bg-accent animate-pulse" : "bg-red-500"
          }`} />
          <span className="text-sm font-medium">{w.worker_id}</span>
          {w.gpu_name && (
            <span className="text-xs text-text-muted">({w.gpu_name})</span>
          )}
          {w.worker_version && (
            <span className="text-[10px] text-text-muted/70">v{w.worker_version}</span>
          )}
        </div>
        <Badge variant={statusVariant as any}>
          {isStale && <AlertTriangle className="h-3 w-3 mr-1" />}
          {statusLabel}
        </Badge>
      </div>

      {/* Current activity */}
      {w.current_activity && (
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <Activity className="h-3.5 w-3.5 text-accent-warm" />
          <span>{w.current_activity}</span>
        </div>
      )}

      {/* Hardware stats — only show for active workers */}
      {isOnline && (
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
      )}

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
        <div className="flex items-center gap-1 text-[10px] text-text-muted">
          <Clock className="h-3 w-3" />
          <span>
            {isStale
              ? `Sem heartbeat há ${formatLongDuration(w.stale_seconds)}`
              : `Heartbeat: ${timeAgo(w.last_heartbeat)}`
            }
          </span>
        </div>
      )}
    </div>
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

function formatLongDuration(seconds: number | null): string {
  if (seconds == null) return "?";
  if (seconds < 60) return `${seconds}s`;
  const min = Math.floor(seconds / 60);
  if (min < 60) return `${min}min`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const days = Math.floor(hr / 24);
  return `${days}d`;
}
