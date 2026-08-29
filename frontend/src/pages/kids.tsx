import { useState, useRef, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { useLiveData } from "@/hooks/useLiveData";
import { Badge, Button, Card, Spinner, EmptyState } from "@/components/ui";
import { toast } from "sonner";
import { useUploadStore, type UploadItem } from "@/lib/upload-store";
import {
  Upload,
  Trash2,
  Image as ImageIcon,
  Video as VideoIcon,
  Sparkles,
  Loader2,
  X,
  Settings,
  Brain,
  Save,
  AlertCircle,
  CheckCircle,
  Clock,
  Film,
  Tag,
  Activity,
  Cpu,
  Eye,
  Monitor,
  Server,
  Users,
  Info,
} from "lucide-react";

const TOPIC_LIBRARY_CATEGORIES = [
  { value: "animals", label: "Animais" },
  { value: "science", label: "Ciência" },
  { value: "space", label: "Espaço" },
  { value: "dinosaurs", label: "Dinossauros" },
  { value: "nature", label: "Natureza" },
  { value: "ocean", label: "Oceano" },
  { value: "human_body", label: "Corpo Humano" },
  { value: "history", label: "História" },
  { value: "geography", label: "Geografia" },
  { value: "vehicles", label: "Veículos" },
  { value: "food", label: "Comida" },
  { value: "colors", label: "Cores" },
  { value: "numbers", label: "Números" },
  { value: "curiosity", label: "Curiosidades" },
];

type Tab = "media" | "config";

export function KidsPage() {
  const [tab, setTab] = useState<Tab>("media");

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Conteúdo Kids</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Gerencie suas mídias e o perfil do canal
        </p>
        <span className="mt-1 inline-flex items-center gap-1.5 rounded-md bg-accent/10 border border-accent/20 px-2 py-0.5 text-[10px] font-medium text-accent">
          Kids
        </span>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        <TabButton active={tab === "media"} onClick={() => setTab("media")} icon={<Film className="h-4 w-4" />} label="Mídias" />
        <TabButton active={tab === "config"} onClick={() => setTab("config")} icon={<Settings className="h-4 w-4" />} label="Configuração do Canal" />
      </div>

      {tab === "media" && <MediaLibrarySection />}
      {tab === "config" && <ChannelConfigSection />}
    </div>
  );
}

// ── Media Library Section ─────────────────────────────────────────────────────

function MediaLibrarySection() {
  const { uploads, addUpload, updateUpload, removeUpload } = useUploadStore();
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const [tagsInput, setTagsInput] = useState("");
  const [descInput, setDescInput] = useState("");
  const [filterKind, setFilterKind] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");

  const params: any = { include_public: true };
  if (filterKind) params.media_kind = filterKind;
  if (filterStatus) params.status = filterStatus;
  const { data, isLoading, refetch } = useLiveData(['kids-assets', filterKind, filterStatus], () => api.listKidsLibraryAssets(params), ['gameplay.status_changed', 'job.status_changed']);

  // Separate own vs public (same pattern as content.tsx)
  const allAssets = data?.assets || [];
  const assets = allAssets.filter((a: any) => a.is_own !== false);
  const publicAssets = allAssets.filter((a: any) => a.is_own === false);

  const kidsUploads = uploads.filter((u) => u.kind === "kids");
  const hasActiveUploads = kidsUploads.some(
    (u) => u.status === "preparing" || u.status === "uploading" || u.status === "processing"
  );

  // Processing count (queued/processing/mapping)
  const processingCount = assets.filter((a: any) =>
    ["queued", "processing", "mapping"].includes(a.processing_status)
  ).length;

  const uploadFile = async (file: File) => {
    const id = `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const item: UploadItem = {
      id,
      fileName: file.name,
      fileSize: file.size,
      progress: 0,
      status: "preparing",
      kind: "kids",
    };
    addUpload(item);

    try {
      await api.uploadKidsLibraryAsset(
        file,
        { tags: tagsInput, description: descInput },
        (loaded, total, pct) => {
          updateUpload(id, { progress: pct, status: "uploading" });
          if (pct >= 100) {
            updateUpload(id, { status: "processing" });
          }
        },
      );
      updateUpload(id, { status: "done", progress: 100 });
      toast.success(`"${file.name}" enviado com sucesso`);
      setTimeout(() => removeUpload(id), 5000);
      await refetch();
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
  }, [tagsInput, descInput]);

  const handleCreateMappingJob = async (assetId: number, filename: string) => {
    try {
      await api.createKidsMappingJob(assetId);
      toast.success(`Mapeamento solicitado para "${filename}". O worker processará em breve.`);
      await refetch();
    } catch (err: any) {
      toast.error(err.message || "Erro ao solicitar mapeamento");
    }
  };

  return (
    <div className="space-y-4">
      {/* How it works banner */}
      <Card className="!p-4 border-accent/20 bg-accent/5">
        <div className="flex items-start gap-3">
          <Info className="h-5 w-5 text-accent shrink-0 mt-0.5" />
          <div className="text-xs text-text-secondary">
            <p className="font-medium text-text">Como funciona</p>
            <p className="mt-1">
              <strong>1.</strong> Envie vídeos e imagens · <strong>2.</strong> Adicione <strong>tags</strong> (ex: dinossauro, floresta) — é assim que o sistema encontra as melhores mídias pra cada ideia · <strong>3.</strong> Vídeos são mapeados automaticamente (VLM analisa cada cena)
            </p>
            <p className="mt-1 text-text-muted">
              Mídias sem tags = <strong>gerais</strong> (usadas como fallback pra qualquer vídeo, igual gameplays genéricas nos Games)
            </p>
          </div>
        </div>
      </Card>

      {/* Upload zone */}
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
              : "border-border hover:border-accent/50 hover:bg-surface/50")
        }`}
      >
        <input
          ref={fileInput}
          type="file"
          accept="image/*,video/*"
          multiple
          className="hidden"
          onChange={(e) => { handleFiles(e.target.files); if (e.target) e.target.value = ""; }}
        />
        <Upload className={`mx-auto h-10 w-10 mb-3 ${dragging ? "text-accent" : "text-text-muted"}`} />
        <p className="text-sm font-medium">
          {hasActiveUploads ? "Enviando mídias..." : "Arraste imagens e vídeos aqui"}
        </p>
        <p className="mt-1 text-xs text-text-muted">
          ou clique para selecionar — PNG, JPEG, WebP, GIF, MP4, WebM, MOV
        </p>
      </div>

      {/* Upload metadata inputs */}
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="text-xs font-medium text-text-secondary">
            Tags (separadas por vírgula) — categoriza a mídia
          </label>
          <input
            type="text"
            value={tagsInput}
            onChange={(e) => setTagsInput(e.target.value)}
            placeholder="Ex: dinossauro, floresta, verde — vazio = geral"
            className="mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none"
          />
        </div>
        <div>
          <label className="text-xs font-medium text-text-secondary">Descrição (opcional)</label>
          <input
            type="text"
            value={descInput}
            onChange={(e) => setDescInput(e.target.value)}
            placeholder="Ex: Tiranossauro em floresta"
            className="mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none"
          />
        </div>
      </div>

      {/* Active uploads */}
      {kidsUploads.length > 0 && (
        <div className="space-y-2">
          {kidsUploads.map((u) => (
            <div key={u.id} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2">
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium truncate">{u.fileName}</p>
                <div className="mt-1 h-1.5 rounded-full bg-border overflow-hidden">
                  <div
                    className={`h-full transition-all ${u.status === "error" ? "bg-red-500" : u.status === "done" ? "bg-green-500" : "bg-accent"}`}
                    style={{ width: `${u.progress}%` }}
                  />
                </div>
              </div>
              <span className="text-[10px] text-text-muted flex-shrink-0">
                {u.status === "done" ? "Concluído" : u.status === "error" ? "Erro" : `${u.progress}%`}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Processing banner (same as content.tsx) */}
      {processingCount > 0 && (
        <div className="flex items-center gap-3 rounded-xl border border-accent-warm/30 bg-accent-warm/5 px-4 py-3">
          <Loader2 className="h-4 w-4 text-accent-warm animate-spin" />
          <span className="text-sm text-accent-warm">
            {processingCount} mídia(s) em processamento — a análise leva alguns minutos
          </span>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => setFilterKind("")}
          className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${!filterKind ? "bg-accent/10 text-accent border border-accent/30" : "text-text-muted hover:text-text border border-transparent"}`}
        >
          Todos
        </button>
        <button
          onClick={() => setFilterKind("image")}
          className={`flex items-center gap-1 rounded-md px-3 py-1 text-xs font-medium transition-colors ${filterKind === "image" ? "bg-accent/10 text-accent border border-accent/30" : "text-text-muted hover:text-text border border-transparent"}`}
        >
          <ImageIcon className="h-3 w-3" /> Imagens
        </button>
        <button
          onClick={() => setFilterKind("video")}
          className={`flex items-center gap-1 rounded-md px-3 py-1 text-xs font-medium transition-colors ${filterKind === "video" ? "bg-accent/10 text-accent border border-accent/30" : "text-text-muted hover:text-text border border-transparent"}`}
        >
          <VideoIcon className="h-3 w-3" /> Vídeos
        </button>
        <div className="flex-1" />
        <button
          onClick={() => setFilterStatus(filterStatus === "ready" ? "" : "ready")}
          className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${filterStatus === "ready" ? "bg-accent/10 text-accent border border-accent/30" : "text-text-muted hover:text-text border border-transparent"}`}
        >
          Só prontos
        </button>
      </div>

      {/* Own assets list — same pattern as content.tsx (horizontal cards) */}
      <div>
        <h2 className="mb-4 text-lg font-semibold">
          Mídias {assets && `(${assets.length})`}
        </h2>
        {isLoading && !data ? (
          <div className="flex justify-center py-12">
            <Spinner className="h-6 w-6" />
          </div>
        ) : assets.length === 0 ? (
          <EmptyState
            icon={<Film className="h-8 w-8" />}
            title="Nenhuma mídia na biblioteca"
            description="Envie imagens e vídeos para a biblioteca do canal. Adicione tags para categorizar — o sistema usa as tags pra encontrar as melhores mídias pra cada ideia."
          />
        ) : (
          <div className="grid gap-3">
            {assets.map((a: any) => (
              <MediaLibraryCard
                key={a.id}
                asset={a}
                onDeleted={refetch}
                onCreateMappingJob={handleCreateMappingJob}
              />
            ))}
          </div>
        )}
      </div>

      {/* Public community assets (same pattern as content.tsx) */}
      {publicAssets.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-text-muted" />
            <h2 className="text-sm font-semibold">Mídias públicas da comunidade</h2>
            <Badge variant="default">{publicAssets.length}</Badge>
          </div>
          <div className="grid gap-3">
            {publicAssets.map((a: any) => (
              <MediaLibraryCard
                key={a.id}
                asset={a}
                onDeleted={refetch}
                onCreateMappingJob={() => {}}
                readOnly
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Processing status config (same pattern as content.tsx) ───────────────────

const KIDS_PROCESSING_STATUS_CONFIG: Record<string, { variant: "default" | "success" | "warning" | "error" | "info"; label: string }> = {
  uploading: { variant: "info", label: "Enviando" },
  queued: { variant: "info", label: "Na fila" },
  processing: { variant: "info", label: "Processando" },
  mapping: { variant: "info", label: "Mapeando" },
  ready: { variant: "success", label: "Pronto" },
  failed: { variant: "error", label: "Falhou" },
};

// ── Kids Mapping Timeline (same as content.tsx MappingTimeline) ──────────────

const KIDS_EVENT_TYPE_COLORS: Record<string, string> = {
  VISUAL_ACTION: "text-red-400 bg-red-500/10 border-red-500/30",
  NARRATION: "text-cyan-400 bg-cyan-500/10 border-cyan-500/30",
  ANIMATION: "text-purple-400 bg-purple-500/10 border-purple-500/30",
  STATIC_IMAGE: "text-text-muted bg-surface-elevated border-border",
  TEXT_OVERLAY: "text-yellow-400 bg-yellow-500/10 border-yellow-500/30",
  TRANSITION: "text-blue-400 bg-blue-500/10 border-blue-500/30",
  CHARACTER_INTRO: "text-green-400 bg-green-500/10 border-green-500/30",
  EDUCATIONAL_DEMO: "text-accent bg-accent/10 border-accent/30",
  UNKNOWN: "text-text-muted bg-surface-elevated border-border",
};

function KidsMappingTimeline({ assetId, filename }: { assetId: number; filename: string }) {
  const [events, setEvents] = useState<any[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadEvents = async () => {
    if (events !== null) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getKidsAssetEvents(assetId);
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
              const color = KIDS_EVENT_TYPE_COLORS[e.event_type]?.split(" ")[1] || "bg-surface-elevated";
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
              const colorClass = KIDS_EVENT_TYPE_COLORS[e.event_type] || KIDS_EVENT_TYPE_COLORS.UNKNOWN;
              return (
                <div key={i} className="flex gap-2 rounded-md px-2 py-1.5 hover:bg-surface-hover">
                  <span className="flex-shrink-0 font-mono text-[10px] text-text-muted pt-0.5">
                    {e.start_time.toFixed(0)}s
                  </span>
                  <span className={`flex-shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-semibold ${colorClass}`}>
                    {e.event_type}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-text-secondary leading-snug">{e.description}</p>
                    {e.transcript && (
                      <p className="mt-0.5 text-[10px] text-text-muted italic">"{e.transcript.substring(0, 80)}..."</p>
                    )}
                  </div>
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

// ── Media Library Card (horizontal, same pattern as content.tsx) ────────────

function MediaLibraryCard({
  asset,
  onDeleted,
  onCreateMappingJob,
  readOnly,
}: {
  asset: any;
  onDeleted: () => void;
  onCreateMappingJob: (assetId: number, filename: string) => void;
  readOnly?: boolean;
}) {
  const [deleting, setDeleting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [tags, setTags] = useState((asset.tags || []).join(", "));
  const [desc, setDesc] = useState(asset.description || "");
  const [saving, setSaving] = useState(false);

  const procCfg = KIDS_PROCESSING_STATUS_CONFIG[asset.processing_status] || KIDS_PROCESSING_STATUS_CONFIG.uploading;
  const isProcessing = asset.processing_status === "processing";
  const isMapping = asset.processing_status === "mapping" || asset.processing_status === "queued";
  const isReady = asset.processing_status === "ready";
  const isFailed = asset.processing_status === "failed";
  // Can request mapping when video is ready but has no events
  const canMap = isReady && asset.media_kind === "video" && (asset.event_count === 0 || asset.event_count === undefined);

  const handleDelete = async () => {
    if (!confirm(`Excluir "${asset.filename}"?`)) return;
    setDeleting(true);
    try {
      await api.deleteKidsAsset(asset.id);
      toast.success("Mídia excluída");
      await onDeleted();
    } catch (err: any) {
      toast.error(err.message || "Erro ao excluir");
    } finally {
      setDeleting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.patchKidsAsset(asset.id, {
        tags: tags.split(",").map((t: string) => t.trim()).filter(Boolean),
        description: desc,
      });
      toast.success("Mídia atualizada");
      setEditing(false);
      await onDeleted();
    } catch (err: any) {
      toast.error(err.message || "Erro ao atualizar");
    } finally {
      setSaving(false);
    }
  };

  const handleToggleVisibility = async () => {
    try {
      await api.toggleKidsAssetVisibility(asset.id, !asset.is_public);
      toast.success(`"${asset.filename}" agora é ${!asset.is_public ? "pública" : "privada"}.`);
      await onDeleted();
    } catch (err: any) {
      toast.error(err.message || "Erro ao alterar visibilidade");
    }
  };

  return (
    <Card className={`!p-4 ${readOnly ? "opacity-80" : ""}`}>
      <div className="flex items-center gap-4">
        {/* Thumbnail / icon */}
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-surface-elevated border border-border overflow-hidden">
          {asset.thumbnail_key ? (
            <img
              src={api.getKidsAssetThumbnailUrl(asset.thumbnail_key)}
              alt={asset.filename}
              className="h-full w-full object-cover"
            />
          ) : isMapping ? (
            <Cpu className="h-5 w-5 text-accent animate-pulse" />
          ) : isReady ? (
            asset.media_kind === "image" ? <ImageIcon className="h-5 w-5 text-accent" /> : <CheckCircle className="h-5 w-5 text-accent" />
          ) : isProcessing ? (
            <Loader2 className="h-5 w-5 text-accent-warm animate-spin" />
          ) : isFailed ? (
            <AlertCircle className="h-5 w-5 text-red-400" />
          ) : asset.media_kind === "image" ? (
            <ImageIcon className="h-5 w-5 text-text-muted" />
          ) : (
            <VideoIcon className="h-5 w-5 text-text-muted" />
          )}
        </div>

        {/* Info */}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium font-mono">{asset.filename}</p>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
            {asset.media_kind === "video" && asset.duration > 0 && (
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" /> {asset.duration.toFixed(1)}s
              </span>
            )}
            {asset.width > 0 && asset.height > 0 && (
              <span>{asset.width}×{asset.height}</span>
            )}
            {asset.file_size > 0 && (
              <span>{(asset.file_size / 1024 / 1024).toFixed(1)}MB</span>
            )}
            {/* Event count badge for mapped videos */}
            {isReady && asset.media_kind === "video" && asset.event_count > 0 && (
              <span className="flex items-center gap-1 text-accent">
                <Activity className="h-3 w-3" /> {asset.event_count} eventos
              </span>
            )}
            {/* Tags — the primary categorization (like game badge in Games) */}
            {asset.tags && asset.tags.length > 0 ? (
              <span className="flex items-center gap-1">
                <Tag className="h-3 w-3" /> {asset.tags.slice(0, 3).join(", ")}
                {asset.tags.length > 3 && ` +${asset.tags.length - 3}`}
              </span>
            ) : (
              <span className="flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5 text-[10px] text-text-muted">
                Geral — sem tags (fallback)
              </span>
            )}
          </div>
        </div>

        {/* Status + actions */}
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <Badge variant={procCfg.variant}>
            {(isProcessing || isMapping) && <Loader2 className="h-3 w-3 animate-spin" />}
            {procCfg.label}
          </Badge>
          {readOnly ? (
            <Badge variant="default">
              <Eye className="h-3 w-3" /> Pública
            </Badge>
          ) : isReady && !editing ? (
            <div className="flex items-center gap-1">
              <button
                onClick={() => setEditing(true)}
                className="text-text-muted hover:text-accent transition-colors p-1 rounded"
                title="Editar tags e descrição"
              >
                <Tag className="h-4 w-4" />
              </button>
              <button
                onClick={handleToggleVisibility}
                className={`transition-colors p-1 rounded ${asset.is_public ? "text-accent" : "text-text-muted hover:text-accent"}`}
                title={asset.is_public ? "Pública — clique para tornar privada" : "Privada — clique para tornar pública"}
              >
                <Eye className="h-4 w-4" />
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="text-text-muted hover:text-red-400 transition-colors p-1 rounded"
                title="Deletar mídia"
              >
                {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {/* Progress bar during processing/mapping */}
      {(isProcessing || isMapping) && (
        <div className="mt-3 h-1 overflow-hidden rounded-full bg-surface-elevated">
          <div className={`h-full ${isMapping ? "w-2/3" : "w-1/3"} animate-pulse-glow rounded-full ${isMapping ? "bg-accent" : "bg-accent-warm"}`} />
        </div>
      )}

      {/* Error message */}
      {isFailed && asset.process_error && (
        <p className="mt-2 text-xs text-red-400">{asset.process_error}</p>
      )}

      {/* Solicitar mapeamento button (same as content.tsx canMap) */}
      {canMap && !readOnly && (
        <div className="mt-3 flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onCreateMappingJob(asset.id, asset.filename)}
          >
            <Cpu className="h-3.5 w-3.5" /> Solicitar mapeamento
          </Button>
          <span className="text-xs text-text-muted">
            Envia para o worker analisar (VLM + ASR)
          </span>
        </div>
      )}

      {/* Edit mode (tags + description) */}
      {editing && isReady && (
        <div className="mt-3 space-y-2 border-t border-border pt-3">
          <input
            type="text"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="tags (vírgula)"
            className="w-full rounded border border-border bg-surface px-2 py-1 text-xs focus:border-accent focus:outline-none"
          />
          <input
            type="text"
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="descrição"
            className="w-full rounded border border-border bg-surface px-2 py-1 text-xs focus:border-accent focus:outline-none"
          />
          <div className="flex gap-1">
            <Button variant="outline" size="sm" className="flex-1 !py-1" onClick={() => setEditing(false)}>
              Cancelar
            </Button>
            <Button variant="primary" size="sm" className="flex-1 !py-1" onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Salvar"}
            </Button>
          </div>
        </div>
      )}

      {/* Mapping timeline (expandable) — same as content.tsx */}
      {isReady && asset.media_kind === "video" && (
        <KidsMappingTimeline assetId={asset.id} filename={asset.filename} />
      )}
    </Card>
  );
}

// ── Tab Button ───────────────────────────────────────────────────────────────

function TabButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
        active
          ? "border-accent text-accent"
          : "border-transparent text-text-muted hover:text-text"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

// ── Channel Config Section ───────────────────────────────────────────────────

function ChannelConfigSection() {
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);

  const [profileForm, setProfileForm] = useState({
    channel_description: "",
    niche: "",
    target_audience: "",
    tone_of_voice: "",
    narrative_style: "",
    content_goals: "",
    special_rules: "",
  });

  const [kidsMeta, setKidsMeta] = useState({
    age_range: "3-6",
    categories: [] as string[],
    target_duration: 45,
  });

  useEffect(() => {
    api.getChannelProfile()
      .then((p) => {
        setProfile(p);
        setProfileForm({
          channel_description: p.channel_description || "",
          niche: p.niche || "",
          target_audience: p.target_audience || "",
          tone_of_voice: p.tone_of_voice || "",
          narrative_style: p.narrative_style || "",
          content_goals: p.content_goals || "",
          special_rules: p.special_rules || "",
        });
        const meta = p.metadata || {};
        setKidsMeta({
          age_range: meta.age_range || meta.kids_age_range || "3-6",
          categories: meta.categories || [],
          target_duration: meta.target_duration || 45,
        });
      })
      .catch((err) => toast.error(err.message))
      .finally(() => setLoading(false));
  }, []);

  const handleSaveProfile = async () => {
    setSavingProfile(true);
    try {
      await api.updateChannelProfile({
        ...profileForm,
        metadata: {
          age_range: kidsMeta.age_range,
          categories: kidsMeta.categories,
          target_duration: kidsMeta.target_duration,
        },
      });
      toast.success("Perfil editorial salvo");
    } catch (err: any) {
      toast.error(err.message || "Erro ao salvar perfil");
    } finally {
      setSavingProfile(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }

  const isProfileEmpty = !profile?.niche && !profile?.target_audience && !profile?.tone_of_voice;

  return (
    <div className="space-y-6">
      {/* Onboarding Alert */}
      {isProfileEmpty && (
        <Card className="!p-4 border-amber-500/30 bg-amber-500/5">
          <div className="flex items-start gap-3">
            <Brain className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
            <div>
              <h3 className="text-sm font-semibold text-amber-300">Configure seu canal para começar</h3>
              <p className="text-xs text-text-muted mt-1">
                Preencha o perfil editorial abaixo para que a IA gere ideias relevantes para o seu canal.
                Sem isso, a descoberta funciona mas sem direcionamento editorial.
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* Editorial Profile */}
      <Card>
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Brain className="h-4 w-4 text-accent" />
              Identidade do Canal
            </h2>
            <p className="mt-1 text-xs text-text-muted">
              Define como a IA personaliza ideias e roteiros para o seu canal
            </p>
          </div>
          <Button size="sm" onClick={handleSaveProfile} disabled={savingProfile}>
            {savingProfile ? <><Spinner className="h-3.5 w-3.5" /> Salvando...</> : <><Save className="h-3.5 w-3.5" /> Salvar</>}
          </Button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-text-secondary">
              Descrição do canal
            </label>
            <textarea
              value={profileForm.channel_description}
              onChange={(e) => setProfileForm({ ...profileForm, channel_description: e.target.value })}
              placeholder="Ex: Canal educativo infantil sobre ciência, natureza e curiosidades. Vídeos curtos e divertidos para crianças de 6-10 anos."
              rows={3}
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Nicho</label>
              <input
                value={profileForm.niche}
                onChange={(e) => setProfileForm({ ...profileForm, niche: e.target.value })}
                placeholder="Ex: Ciência e natureza para crianças"
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Público-alvo</label>
              <input
                value={profileForm.target_audience}
                onChange={(e) => setProfileForm({ ...profileForm, target_audience: e.target.value })}
                placeholder="Ex: Crianças de 6-10 anos e seus pais"
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
              />
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Tom de voz</label>
              <input
                value={profileForm.tone_of_voice}
                onChange={(e) => setProfileForm({ ...profileForm, tone_of_voice: e.target.value })}
                placeholder="Ex: amigável, curioso, divertido"
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Estilo de narrativa</label>
              <input
                value={profileForm.narrative_style}
                onChange={(e) => setProfileForm({ ...profileForm, narrative_style: e.target.value })}
                placeholder="Ex: perguntas e respostas, descoberta"
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-text-secondary">Objetivos de conteúdo</label>
            <input
              value={profileForm.content_goals}
              onChange={(e) => setProfileForm({ ...profileForm, content_goals: e.target.value })}
              placeholder="Ex: Educar e entreter, despertar curiosidade científica"
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
            />
          </div>
        </div>
      </Card>

      {/* Kids-specific Config */}
      <Card>
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Sparkles className="h-4 w-4 text-accent" />
              Configuração Kids
            </h2>
            <p className="mt-1 text-xs text-text-muted">
              Faixa etária, categorias e duração para a descoberta de ideias
            </p>
          </div>
          <Button size="sm" onClick={handleSaveProfile} disabled={savingProfile}>
            {savingProfile ? <><Spinner className="h-3.5 w-3.5" /> Salvando...</> : <><Save className="h-3.5 w-3.5" /> Salvar</>}
          </Button>
        </div>

        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Faixa etária alvo</label>
              <select
                value={kidsMeta.age_range}
                onChange={(e) => setKidsMeta({ ...kidsMeta, age_range: e.target.value })}
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
              >
                <option value="3-6">3-6 anos</option>
                <option value="6-10">6-10 anos</option>
                <option value="7-10">7-10 anos</option>
                <option value="all">Todas as idades</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">
                Duração alvo (segundos): {kidsMeta.target_duration}
              </label>
              <input
                type="range"
                min={15}
                max={90}
                step={5}
                value={kidsMeta.target_duration}
                onChange={(e) => setKidsMeta({ ...kidsMeta, target_duration: Number(e.target.value) })}
                className="mt-2 w-full"
              />
            </div>
          </div>

          <div>
            <label className="mb-2 block text-xs font-medium text-text-secondary">
              Categorias de interesse (vazio = todas)
            </label>
            <div className="flex flex-wrap gap-2">
              {TOPIC_LIBRARY_CATEGORIES.map((c) => (
                <button
                  key={c.value}
                  onClick={() => {
                    setKidsMeta((prev) => ({
                      ...prev,
                      categories: prev.categories.includes(c.value)
                        ? prev.categories.filter((v) => v !== c.value)
                        : [...prev.categories, c.value],
                    }));
                  }}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                    kidsMeta.categories.includes(c.value)
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border bg-surface text-text-muted hover:text-text"
                  }`}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}
