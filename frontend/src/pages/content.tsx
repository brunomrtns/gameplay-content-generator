import { useState, useRef, useCallback, useEffect } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { Badge, Button, Card, Spinner, EmptyState } from "@/components/ui";
import { fmtDuration, fmtBytes } from "@/lib/utils";
import { useUploadStore, type UploadItem } from "@/lib/upload-store";
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
  Cpu,
  Server,
  BookOpen,
  Brain,
  Trash2,
  FileText,
  Save,
  Search,
  ChevronDown,
  ChevronRight,
  Eye,
  Activity,
  Users,
  Gamepad2,
  Pencil,
  Power,
  PowerOff,
} from "lucide-react";
import { GameSearchModal, type CatalogGame } from "@/components/game-search-modal";

const STATUS_CONFIG: Record<string, { variant: "default" | "success" | "warning" | "error" | "info"; label: string }> = {
  discovered: { variant: "info", label: "Descoberto" },
  probing: { variant: "info", label: "Analisando" },
  ready: { variant: "success", label: "Pronto" },
  error: { variant: "error", label: "Erro" },
  needs_review: { variant: "warning", label: "Revisão" },
  duplicate: { variant: "default", label: "Duplicado" },
};

const PROCESSING_STATUS_CONFIG: Record<string, { variant: "default" | "success" | "warning" | "error" | "info"; label: string }> = {
  uploading: { variant: "info", label: "Enviando" },
  uploaded: { variant: "info", label: "Aguardando worker" },
  waiting_worker: { variant: "info", label: "Na fila" },
  downloading: { variant: "info", label: "Baixando" },
  downloaded: { variant: "info", label: "Baixado" },
  mapping: { variant: "info", label: "Mapeando" },
  mapped: { variant: "success", label: "Mapeado" },
  ready: { variant: "success", label: "Pronto" },
  generating: { variant: "info", label: "Gerando vídeo" },
  finished: { variant: "success", label: "Vídeo pronto" },
  failed: { variant: "error", label: "Falhou" },
};

const KNOWLEDGE_STATUS_CONFIG: Record<string, { variant: "default" | "success" | "warning" | "error" | "info"; label: string }> = {
  pending: { variant: "info", label: "Pendente" },
  processing: { variant: "info", label: "Processando" },
  indexed: { variant: "success", label: "Indexado" },
  error: { variant: "error", label: "Erro" },
};

type Tab = "media" | "channel";

export function ContentPage() {
  const [tab, setTab] = useState<Tab>("media");

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Conteúdo</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Gerencie suas mídias e o perfil do canal
        </p>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        <TabButton
          active={tab === "media"}
          onClick={() => setTab("media")}
          icon={<Film className="h-4 w-4" />}
          label="Gravações"
        />
        <TabButton
          active={tab === "channel"}
          onClick={() => setTab("channel")}
          icon={<Brain className="h-4 w-4" />}
          label="Identidade do Canal"
        />
      </div>

      {/* Tab content */}
      {tab === "media" && <MediaTab />}
      {tab === "channel" && <ChannelProfileSection />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-all border-b-2 -mb-px ${
        active
          ? "border-accent text-accent"
          : "border-transparent text-text-muted hover:text-text-secondary"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

// ── Mapping Timeline (expandable view of VLM analysis) ─────────────────────

const EVENT_TYPE_COLORS: Record<string, string> = {
  COMBAT: "text-red-400 bg-red-500/10 border-red-500/30",
  VEHICLE: "text-blue-400 bg-blue-500/10 border-blue-500/30",
  IDLE: "text-text-muted bg-surface-elevated border-border",
  CUTSCENE: "text-purple-400 bg-purple-500/10 border-purple-500/30",
  MENU: "text-yellow-400 bg-yellow-500/10 border-yellow-500/30",
  EXPLORATION: "text-green-400 bg-green-500/10 border-green-500/30",
  DIALOGUE: "text-cyan-400 bg-cyan-500/10 border-cyan-500/30",
};

function MappingTimeline({ sourceId, filename }: { sourceId: number; filename: string }) {
  const [events, setEvents] = useState<any[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadEvents = async () => {
    if (events !== null) return; // already loaded
    setLoading(true);
    setError(null);
    try {
      const res = await api.getSourceEvents(sourceId);
      setEvents(res.events || []);
    } catch (e: any) {
      setError(e.message || "Erro ao carregar eventos");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mt-3 border-t border-border pt-3">
      <div
        className="flex cursor-pointer items-center gap-2 text-xs font-medium text-text-secondary hover:text-text"
        onClick={loadEvents}
      >
        <Activity className="h-3.5 w-3.5" />
        {events === null && !loading && "Ver análise do mapeamento"}
        {loading && "Carregando..."}
        {error && <span className="text-red-400">{error}</span>}
        {events !== null && `${events.length} eventos detectados`}
      </div>

      {events !== null && events.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {/* Timeline bar */}
          <div className="flex h-2 w-full overflow-hidden rounded-full bg-surface-elevated">
            {events.map((e, i) => {
              const color = EVENT_TYPE_COLORS[e.event_type]?.split(" ")[1] || "bg-surface-elevated";
              return (
                <div
                  key={i}
                  className={color}
                  style={{ flex: Math.max(1, (e.end_time - e.start_time) / 10) }}
                  title={`${e.event_type} [${e.start_time.toFixed(0)}-${e.end_time.toFixed(0)}s]`}
                />
              );
            })}
          </div>

          {/* Event list */}
          <div className="max-h-64 overflow-y-auto space-y-1.5 rounded-lg border border-border bg-surface p-2">
            {events.map((e, i) => {
              const colorClass = EVENT_TYPE_COLORS[e.event_type] || EVENT_TYPE_COLORS.IDLE;
              return (
                <div key={i} className="flex gap-2 rounded-md px-2 py-1.5 hover:bg-surface-hover">
                  {/* Time */}
                  <span className="flex-shrink-0 font-mono text-[10px] text-text-muted pt-0.5">
                    {e.start_time.toFixed(0)}s
                  </span>
                  {/* Type badge */}
                  <span className={`flex-shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-semibold ${colorClass}`}>
                    {e.event_type}
                  </span>
                  {/* Description */}
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-text-secondary leading-snug">{e.description}</p>
                    {e.transcript && (
                      <p className="mt-0.5 text-[10px] text-text-muted italic">"{e.transcript.substring(0, 80)}..."</p>
                    )}
                  </div>
                  {/* Interesting score */}
                  {e.interesting_score >= 0.7 && (
                    <span className="flex-shrink-0 text-[9px] text-accent font-semibold">
                      ★ {e.interesting_score.toFixed(1)}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {events !== null && events.length === 0 && (
        <p className="mt-2 text-xs text-text-muted">Nenhum evento encontrado.</p>
      )}
    </div>
  );
}


// ── Media Tab (gameplay upload + list) ───────────────────────────────────────

function MediaTab() {
  const { data: allSources, loading, refetch } = usePoll(() => api.listSources(undefined, undefined, true), 5000);
  const { uploads, addUpload, updateUpload, removeUpload } = useUploadStore();
  const [scanning, setScanning] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  // Game assignment modal
  const [gameModalSourceId, setGameModalSourceId] = useState<number | null>(null);

  // Separate own vs public gameplays
  const sources = (allSources || []).filter((s: any) => s.is_own !== false);
  const publicSources = (allSources || []).filter((s: any) => s.is_own === false);

  const activeUploads = uploads.filter((u) => u.kind === "gameplay" && (u.status === "preparing" || u.status === "uploading" || u.status === "processing"));
  const hasActiveUploads = activeUploads.length > 0;

  const uploadFile = async (file: File) => {
    const id = `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const item: UploadItem = {
      id,
      fileName: file.name,
      fileSize: file.size,
      progress: 0,
      status: "preparing",
      kind: "gameplay",
    };
    addUpload(item);

    try {
      await api.uploadGameplay(file, (loaded, total, pct) => {
        updateUpload(id, { progress: pct, status: "uploading" });
        if (pct >= 100) {
          updateUpload(id, { status: "processing" });
        }
      });
      updateUpload(id, { status: "done", progress: 100 });
      toast.success(`"${file.name}" enviado com sucesso`);
      setTimeout(() => removeUpload(id), 5000);
    } catch (err: any) {
      updateUpload(id, { status: "error", error: err.message || "Erro no upload" });
      toast.error(err.message || `Erro no upload de "${file.name}"`);
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

  const handleCreateMappingJob = async (sourceId: number, filename: string) => {
    try {
      await api.createMappingJob(sourceId);
      toast.success(`Mapeamento solicitado para "${filename}". O worker processará em breve.`);
    } catch (err: any) {
      toast.error(err.message || "Erro ao solicitar mapeamento");
    }
  };

  const handleDeleteSource = async (sourceId: number, filename: string) => {
    if (!window.confirm(
      `Tem certeza que deseja deletar "${filename}"?\n\n` +
      `Isso vai remover TODOS os clips, eventos e arquivos físicos ` +
      `associados a esta gameplay. Esta ação não pode ser desfeita.`
    )) return;
    if (!window.confirm(`Confirma novamente? Os arquivos no HD serão apagados permanentemente.`)) return;
    try {
      await api.deleteSource(sourceId);
      toast.success(`Gameplay "${filename}" deletada. Os arquivos físicos serão removidos pelo worker.`);
    } catch (err: any) {
      toast.error(err.message || "Erro ao deletar gameplay");
    }
  };

  const handleToggleVisibility = async (sourceId: number, isPublic: boolean, filename: string) => {
    try {
      await api.toggleGameplayVisibility(sourceId, isPublic);
      toast.success(`"${filename}" agora é ${isPublic ? "pública" : "privada"}.`);
    } catch (err: any) {
      toast.error(err.message || "Erro ao alterar visibilidade");
    }
  };

  const handleToggleEnabled = async (sourceId: number, enabled: boolean, filename: string) => {
    try {
      await api.toggleGameplayEnabled(sourceId, enabled);
      toast.success(`"${filename}" ${enabled ? "disponível para uso" : "estacionada"}.`);
      refetch();
    } catch (err: any) {
      toast.error(err.message || "Erro ao alterar disponibilidade");
    }
  };

  const handleAssignGame = async (game: CatalogGame) => {
    if (gameModalSourceId === null) return;
    try {
      await api.assignGameByName(gameModalSourceId, game.name, game.slug);
      toast.success(`Jogo definido: ${game.name}`);
      refetch();
    } catch (err: any) {
      toast.error(err.message || "Erro ao atribuir jogo");
    }
  };

  const processing = sources?.filter((s: any) =>
    ["discovered", "probing"].includes(s.ingestion_status)
  ).length || 0;

  return (
    <div className="space-y-6">
      {/* Upload zone */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">Enviar gravações</h2>
          <Button variant="outline" size="sm" onClick={handleScan} disabled={scanning}>
            {scanning ? <><Spinner className="h-3.5 w-3.5" /> Escaneando...</> : <><RefreshCw className="h-3.5 w-3.5" /> Escanear inbox</>}
          </Button>
        </div>
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => !hasActiveUploads && fileInput.current?.click()}
          className={`rounded-xl border-2 border-dashed p-8 text-center transition-all duration-300 ${
            hasActiveUploads
              ? "border-border cursor-default"
              : "cursor-pointer " + (dragging
                ? "border-accent bg-accent/5 scale-[1.01]"
                : "border-border-bright hover:border-accent/50 hover:bg-surface-hover")
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
              {hasActiveUploads ? (
                <Loader2 className="h-6 w-6 text-accent animate-spin" />
              ) : (
                <Upload className="h-6 w-6 text-accent" />
              )}
            </div>
            <div>
              <p className="text-sm font-medium">
                {hasActiveUploads
                  ? `${activeUploads.length} arquivo(s) em upload…`
                  : "Arraste gravações aqui ou clique para enviar"}
              </p>
              {!hasActiveUploads && (
                <p className="mt-1 text-xs text-text-muted">
                  MP4, MKV, MOV, AVI · Análise automática após o upload
                </p>
              )}
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
              const procCfg = PROCESSING_STATUS_CONFIG[s.processing_status] || PROCESSING_STATUS_CONFIG.uploaded;
              const isMapping = ["uploading", "uploaded", "waiting_worker", "downloading", "downloaded", "mapping"].includes(s.processing_status);
              const canMap = s.ingestion_status === "ready" && (s.processing_status === "uploaded" || !s.processing_status);
              const isReady = s.processing_status === "ready" || s.processing_status === "mapped";
              return (
                <Card key={s.id} className="!p-4">
                  <div className="flex items-center gap-4">
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-surface-elevated border border-border">
                      {isMapping ? (
                        <Cpu className="h-5 w-5 text-accent animate-pulse" />
                      ) : isReady ? (
                        <CheckCircle2 className="h-5 w-5 text-accent" />
                      ) : isProcessing ? (
                        <Loader2 className="h-5 w-5 text-accent-warm animate-spin" />
                      ) : s.ingestion_status === "error" ? (
                        <AlertCircle className="h-5 w-5 text-red-400" />
                      ) : (
                        <Film className="h-5 w-5 text-text-muted" />
                      )}
                    </div>
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
                        {s.file_size > 0 && (
                          <span>{fmtBytes(s.file_size)}</span>
                        )}
                        {/* Game badge — clickable to change */}
                        {s.game_name ? (
                          <button
                            onClick={() => setGameModalSourceId(s.id)}
                            className="flex items-center gap-1 rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent transition-colors hover:bg-accent/20"
                            title="Alterar jogo"
                          >
                            <Gamepad2 className="h-3 w-3" />
                            {s.game_name}
                            <Pencil className="h-2.5 w-2.5 opacity-60" />
                          </button>
                        ) : (
                          <button
                            onClick={() => setGameModalSourceId(s.id)}
                            className="flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5 text-xs text-text-muted transition-colors hover:border-accent/40 hover:text-accent"
                            title="Definir jogo"
                          >
                            <Gamepad2 className="h-3 w-3" />
                            Definir jogo
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1.5">
                      <Badge variant={cfg.variant}>
                        {isProcessing && <Loader2 className="h-3 w-3 animate-spin" />}
                        {cfg.label}
                      </Badge>
                      {s.processing_status && s.processing_status !== "ready" && (
                        <Badge variant={procCfg.variant}>
                          {isMapping && <Server className="h-3 w-3 animate-pulse" />}
                          {procCfg.label}
                        </Badge>
                      )}
                      {s.enabled === false && (
                        <Badge variant="warning">
                          <PowerOff className="h-3 w-3" />
                          Estacionada
                        </Badge>
                      )}
                      {!isProcessing && !isMapping && (
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => handleToggleEnabled(s.id, !s.enabled, s.filename)}
                            className={`transition-colors p-1 rounded ${s.enabled ? "text-green-400" : "text-text-muted hover:text-green-400"}`}
                            title={s.enabled ? "Disponível — clique para estacionar" : "Estacionada — clique para disponibilizar"}
                          >
                            {s.enabled ? <Power className="h-4 w-4" /> : <PowerOff className="h-4 w-4" />}
                          </button>
                          <button
                            onClick={() => handleToggleVisibility(s.id, !s.is_public, s.filename)}
                            className={`transition-colors p-1 rounded ${s.is_public ? "text-accent" : "text-text-muted hover:text-accent"}`}
                            title={s.is_public ? "Pública — clique para tornar privada" : "Privada — clique para tornar pública"}
                          >
                            <Eye className="h-4 w-4" />
                          </button>
                          <button
                            onClick={() => handleDeleteSource(s.id, s.filename)}
                            className="text-text-muted hover:text-red-400 transition-colors p-1 rounded"
                            title="Deletar gameplay"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                  {(isProcessing || isMapping) && (
                    <div className="mt-3 h-1 overflow-hidden rounded-full bg-surface-elevated">
                      <div className={`h-full ${isMapping ? "w-2/3" : "w-1/3"} animate-pulse-glow rounded-full ${isMapping ? "bg-accent" : "bg-accent-warm"}`} />
                    </div>
                  )}
                  {canMap && (
                    <div className="mt-3 flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleCreateMappingJob(s.id, s.filename)}
                      >
                        <Cpu className="h-3.5 w-3.5" /> Solicitar mapeamento
                      </Button>
                      <span className="text-xs text-text-muted">
                        Envia para o worker analisar (VLM + ASR)
                      </span>
                    </div>
                  )}
                  {s.error_message && (
                    <p className="mt-2 text-xs text-red-400">{s.error_message}</p>
                  )}
                  {isReady && (
                    <MappingTimeline sourceId={s.id} filename={s.filename} />
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </div>

      {/* Public gameplays from community */}
      {publicSources.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-text-muted" />
            <h2 className="text-sm font-semibold">Gameplays públicas da comunidade</h2>
            <Badge variant="default">{publicSources.length}</Badge>
          </div>
          <div className="grid gap-3">
            {publicSources.map((s: any) => {
              const isReady = s.processing_status === "ready" || s.processing_status === "mapped";
              return (
                <Card key={s.id} className="!p-4 opacity-80">
                  <div className="flex items-center gap-4">
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-surface-elevated border border-border">
                      <CheckCircle2 className="h-5 w-5 text-accent" />
                    </div>
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
                    <div className="flex shrink-0 flex-col items-end gap-1.5">
                      <Badge variant="default">
                        <Eye className="h-3 w-3" /> Pública
                      </Badge>
                    </div>
                  </div>
                  {isReady && (
                    <MappingTimeline sourceId={s.id} filename={s.filename} />
                  )}
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* Game assignment modal */}
      <GameSearchModal
        open={gameModalSourceId !== null}
        onClose={() => setGameModalSourceId(null)}
        onSelect={handleAssignGame}
        title="Definir Jogo da Gravação"
        subtitle="Busque pelo nome ou nome alternativo (GTA, Witcher, etc.)"
      />
    </div>
  );
}

function ChannelProfileSection() {
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    channel_description: "",
    niche: "",
    target_audience: "",
    tone_of_voice: "",
    narrative_style: "",
    content_goals: "",
    special_rules: "",
  });

  useEffect(() => {
    api.getChannelProfile()
      .then((p) => {
        setProfile(p);
        setForm({
          channel_description: p.channel_description || "",
          niche: p.niche || "",
          target_audience: p.target_audience || "",
          tone_of_voice: p.tone_of_voice || "",
          narrative_style: p.narrative_style || "",
          content_goals: p.content_goals || "",
          special_rules: p.special_rules || "",
        });
      })
      .catch((err) => toast.error(err.message))
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateChannelProfile(form);
      toast.success("Perfil do canal salvo");
    } catch (err: any) {
      toast.error(err.message || "Erro ao salvar perfil");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <Card><div className="flex justify-center py-8"><Spinner className="h-6 w-6" /></div></Card>;
  }

  return (
    <Card>
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Brain className="h-4 w-4 text-accent" />
            Identidade do Canal
          </h2>
          <p className="mt-1 text-xs text-text-muted">
            Define como a IA personaliza os roteiros para o seu canal
          </p>
        </div>
        <Button size="sm" onClick={handleSave} disabled={saving}>
          {saving ? <><Spinner className="h-3.5 w-3.5" /> Salvando...</> : <><Save className="h-3.5 w-3.5" /> Salvar</>}
        </Button>
      </div>

      <div className="space-y-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-text-secondary">
            Descrição do canal
          </label>
          <textarea
            value={form.channel_description}
            onChange={(e) => setForm({ ...form, channel_description: e.target.value })}
            placeholder="Ex: Meu canal é focado em análises de partidas competitivas de FPS. Quero vídeos com tom educativo, destacando estratégias e momentos decisivos."
            rows={3}
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-text-secondary">
              Nicho
            </label>
            <input
              value={form.niche}
              onChange={(e) => setForm({ ...form, niche: e.target.value })}
              placeholder="Ex: FPS competitivo"
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-text-secondary">
              Público-alvo
            </label>
            <input
              value={form.target_audience}
              onChange={(e) => setForm({ ...form, target_audience: e.target.value })}
              placeholder="Ex: Jogadores casuais que querem melhorar"
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
            />
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-text-secondary">
              Tom de voz
            </label>
            <input
              value={form.tone_of_voice}
              onChange={(e) => setForm({ ...form, tone_of_voice: e.target.value })}
              placeholder="Ex: educativo, analítico"
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-text-secondary">
              Estilo de narrativa
            </label>
            <input
              value={form.narrative_style}
              onChange={(e) => setForm({ ...form, narrative_style: e.target.value })}
              placeholder="Ex: storytelling, análise direta"
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
            />
          </div>
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-text-secondary">
            Objetivos do canal
          </label>
          <textarea
            value={form.content_goals}
            onChange={(e) => setForm({ ...form, content_goals: e.target.value })}
            placeholder="Ex: Crescer como autoridade em FPS competitivo, educar a audiência"
            rows={2}
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-text-secondary">
            Regras especiais para a IA
          </label>
          <textarea
            value={form.special_rules}
            onChange={(e) => setForm({ ...form, special_rules: e.target.value })}
            placeholder="Ex: Nunca usar gírias de CS:GO se o vídeo é sobre Valorant. Sempre citar o nome do agente."
            rows={2}
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
          />
        </div>
      </div>
    </Card>
  );
}

