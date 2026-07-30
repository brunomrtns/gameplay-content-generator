import { useState, useRef, useCallback } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { Badge, Button, Card, Spinner, EmptyState } from "@/components/ui";
import { fmtDuration } from "@/lib/utils";
import { toast } from "sonner";
import {
  Upload,
  Film,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  Loader2,
  Monitor,
  Clock,
} from "lucide-react";

const STATUS_CONFIG: Record<string, { variant: "default" | "success" | "warning" | "error" | "info"; label: string }> = {
  discovered: { variant: "info", label: "Descoberto" },
  probing: { variant: "info", label: "Analisando" },
  ready: { variant: "success", label: "Pronto" },
  error: { variant: "error", label: "Erro" },
  needs_review: { variant: "warning", label: "Revisão" },
  duplicate: { variant: "default", label: "Duplicado" },
};

export function ContentPage() {
  const { data: sources, loading } = usePoll(() => api.listSources(), 5000);
  const [uploading, setUploading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const uploadFile = async (file: File) => {
    setUploading(true);
    try {
      await api.uploadGameplay(file);
      toast.success(`"${file.name}" enviado com sucesso`);
    } catch (err: any) {
      toast.error(err.message || "Erro no upload");
    } finally {
      setUploading(false);
    }
  };

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    Array.from(files).forEach(uploadFile);
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    handleFiles(e.dataTransfer.files);
  }, []);

  const handleScan = async () => {
    setScanning(true);
    try {
      const r = await api.scanInbox();
      toast.success(`${r.discovered} arquivo(s) encontrado(s)`);
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setScanning(false);
    }
  };

  const processing = sources?.filter((s: any) =>
    ["discovered", "probing"].includes(s.ingestion_status)
  ).length || 0;

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Conteúdo</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Envie gravações de gameplay para alimentar a geração de vídeos
          </p>
        </div>
        <Button variant="outline" onClick={handleScan} disabled={scanning}>
          {scanning ? <><Spinner className="h-4 w-4" /> Escaneando...</> : <><RefreshCw className="h-4 w-4" /> Escanear inbox</>}
        </Button>
      </div>

      {/* Upload zone */}
      <Card>
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileInput.current?.click()}
          className={`cursor-pointer rounded-xl border-2 border-dashed p-12 text-center transition-all duration-300 ${
            dragging
              ? "border-accent bg-accent/5 scale-[1.01]"
              : "border-border-bright hover:border-accent/50 hover:bg-surface-hover"
          }`}
        >
          <input
            ref={fileInput}
            type="file"
            className="hidden"
            multiple
            accept="video/*"
            onChange={(e) => handleFiles(e.target.files)}
          />
          <div className="flex flex-col items-center gap-3">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-elevated border border-border">
              {uploading ? (
                <Loader2 className="h-6 w-6 text-accent animate-spin" />
              ) : (
                <Upload className="h-6 w-6 text-accent" />
              )}
            </div>
            <div>
              <p className="text-sm font-medium">
                {uploading ? "Enviando..." : "Arraste gravações aqui ou clique para enviar"}
              </p>
              <p className="mt-1 text-xs text-text-muted">
                MP4, MKV, MOV, AVI · Análise automática após o upload
              </p>
            </div>
          </div>
        </div>
      </Card>

      {/* Processing banner */}
      {processing > 0 && (
        <div className="flex items-center gap-3 rounded-xl border border-accent-warm/30 bg-accent-warm/5 px-4 py-3">
          <Loader2 className="h-4 w-4 text-accent-warm animate-spin" />
          <span className="text-sm text-accent-warm">
            {processing} gravação(ões) em processamento — a análise leva alguns minutos
          </span>
        </div>
      )}

      {/* Gameplays list */}
      <div>
        <h2 className="mb-4 text-lg font-semibold">
          Gravações {sources && `(${sources.length})`}
        </h2>
        {loading && !sources ? (
          <div className="flex justify-center py-16"><Spinner className="h-8 w-8" /></div>
        ) : !sources || sources.length === 0 ? (
          <Card>
            <EmptyState
              icon={<Film className="h-10 w-10" />}
              title="Nenhuma gravação ainda"
              description="Envie arquivos de gameplay ou escaneie a pasta inbox para descobrir gravações automaticamente."
            />
          </Card>
        ) : (
          <div className="grid gap-3">
            {sources.map((s: any) => {
              const cfg = STATUS_CONFIG[s.ingestion_status] || STATUS_CONFIG.discovered;
              const isProcessing = ["discovered", "probing"].includes(s.ingestion_status);
              return (
                <Card key={s.id} className="!p-4">
                  <div className="flex items-center gap-4">
                    {/* Icon */}
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-surface-elevated border border-border">
                      {isProcessing ? (
                        <Loader2 className="h-5 w-5 text-accent-warm animate-spin" />
                      ) : s.ingestion_status === "ready" ? (
                        <CheckCircle2 className="h-5 w-5 text-accent" />
                      ) : s.ingestion_status === "error" ? (
                        <AlertCircle className="h-5 w-5 text-red-400" />
                      ) : (
                        <Film className="h-5 w-5 text-text-muted" />
                      )}
                    </div>

                    {/* Info */}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium font-mono">{s.filename}</p>
                      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
                        <span className="flex items-center gap-1">
                          <Clock className="h-3 w-3" /> {fmtDuration(s.duration)}
                        </span>
                        {s.width > 0 && (
                          <span className="flex items-center gap-1">
                            <Monitor className="h-3 w-3" /> {s.width}×{s.height}
                          </span>
                        )}
                        {s.game_name && (
                          <span className="text-text-secondary">{s.game_name}</span>
                        )}
                      </div>
                    </div>

                    {/* Status */}
                    <div className="shrink-0">
                      <Badge variant={cfg.variant}>
                        {isProcessing && <Loader2 className="h-3 w-3 animate-spin" />}
                        {cfg.label}
                      </Badge>
                    </div>
                  </div>

                  {/* Progress bar for processing */}
                  {isProcessing && (
                    <div className="mt-3 h-1 overflow-hidden rounded-full bg-surface-elevated">
                      <div className="h-full w-1/3 animate-pulse-glow rounded-full bg-accent-warm" />
                    </div>
                  )}

                  {s.error_message && (
                    <p className="mt-2 text-xs text-red-400">{s.error_message}</p>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
