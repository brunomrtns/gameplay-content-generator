import { useState, useRef, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { Badge, Button, Card, Spinner, EmptyState } from "@/components/ui";
import { toast } from "sonner";
import { useUploadStore, type UploadItem } from "@/lib/upload-store";
import {
  Upload,
  Trash2,
  Image as ImageIcon,
  Video as VideoIcon,
  Plus,
  Sparkles,
  Loader2,
  FileText,
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
} from "lucide-react";

const CATEGORIES = [
  { value: "general", label: "Geral" },
  { value: "educational", label: "Educativo" },
  { value: "animals", label: "Animais" },
  { value: "science", label: "Ciência" },
  { value: "story", label: "História" },
  { value: "alphabet", label: "Alfabeto" },
];

const AGE_RANGES = [
  { value: "3-6", label: "3-6 anos" },
  { value: "7-10", label: "7-10 anos" },
  { value: "all", label: "Todas as idades" },
];

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

type Tab = "topics" | "media" | "config";

export function KidsPage() {
  const [tab, setTab] = useState<Tab>("topics");
  const { data: topicsData, loading, refetch } = usePoll(() => api.listKidsTopics(), 15000);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [expandedTopic, setExpandedTopic] = useState<number | null>(null);

  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("educational");
  const [ageRange, setAgeRange] = useState("3-6");
  const [description, setDescription] = useState("");

  const topics = topicsData?.topics || [];

  const handleCreate = async () => {
    if (!title.trim()) {
      toast.error("Título é obrigatório");
      return;
    }
    setCreating(true);
    try {
      await api.createKidsTopic({ title, category, age_range: ageRange, description });
      toast.success("Tópico criado!");
      setShowCreate(false);
      setTitle("");
      setDescription("");
      await refetch();
    } catch (err: any) {
      toast.error(err.message || "Erro ao criar tópico");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Excluir este tópico? As mídias vinculadas serão desvinculadas (não excluídas).")) return;
    try {
      await api.deleteKidsTopic(id);
      toast.success("Tópico excluído");
      await refetch();
    } catch (err: any) {
      toast.error(err.message || "Erro ao excluir");
    }
  };

  if (loading && !topicsData) {
    return (
      <div className="flex items-center justify-center py-32">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Tópicos Kids</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Crie conteúdo educativo e divertido para crianças
          </p>
          <span className="mt-1 inline-flex items-center gap-1.5 rounded-md bg-accent/10 border border-accent/20 px-2 py-0.5 text-[10px] font-medium text-accent">
            Kids
          </span>
        </div>
        {tab === "topics" && (
          <Button variant="primary" size="lg" onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" /> Novo tópico
          </Button>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        <TabButton active={tab === "topics"} onClick={() => setTab("topics")} icon={<FileText className="h-4 w-4" />} label="Tópicos" />
        <TabButton active={tab === "media"} onClick={() => setTab("media")} icon={<Film className="h-4 w-4" />} label="Mídias" />
        <TabButton active={tab === "config"} onClick={() => setTab("config")} icon={<Settings className="h-4 w-4" />} label="Configuração do Canal" />
      </div>

      {tab === "topics" && (
        <>
          {/* Create dialog */}
          {showCreate && (
            <Card className="!p-6 border-accent/30">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold">Criar novo tópico</h3>
                <button onClick={() => setShowCreate(false)} className="text-text-muted hover:text-text">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="space-y-4">
                <div>
                  <label className="text-xs font-medium text-text-secondary">Título</label>
                  <input
                    type="text"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="Ex: Dinossauros, Sistema Solar, ABC..."
                    className="mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none"
                  />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-xs font-medium text-text-secondary">Categoria</label>
                    <select
                      value={category}
                      onChange={(e) => setCategory(e.target.value)}
                      className="mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none"
                    >
                      {CATEGORIES.map((c) => (
                        <option key={c.value} value={c.value}>{c.label}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-text-secondary">Faixa etária</label>
                    <select
                      value={ageRange}
                      onChange={(e) => setAgeRange(e.target.value)}
                      className="mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none"
                    >
                      {AGE_RANGES.map((a) => (
                        <option key={a.value} value={a.value}>{a.label}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="text-xs font-medium text-text-secondary">Descrição (opcional)</label>
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Descreva o tópico para ajudar o LLM..."
                    rows={3}
                    className="mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:border-accent focus:outline-none"
                  />
                </div>
                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={() => setShowCreate(false)}>Cancelar</Button>
                  <Button variant="primary" onClick={handleCreate} disabled={creating || !title.trim()}>
                    {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : "Criar"}
                  </Button>
                </div>
              </div>
            </Card>
          )}

          {/* Topics grid */}
          {topics.length === 0 ? (
            <EmptyState
              icon={<FileText className="h-8 w-8" />}
              title="Nenhum tópico ainda"
              description="Crie seu primeiro tópico Kids ou vá à aba Ideias para descobrir conteúdo automaticamente. As mídias são enviadas na aba Mídias."
            />
          ) : (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {topics.map((t: any) => (
                <Card key={t.id} className="!p-5">
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex-1">
                      <h3 className="text-sm font-semibold">{t.title}</h3>
                      <div className="mt-1 flex items-center gap-2">
                        <Badge variant="info">{t.category}</Badge>
                        <Badge variant="default">{t.age_range}</Badge>
                      </div>
                    </div>
                    <button
                      onClick={() => handleDelete(t.id)}
                      className="text-text-muted hover:text-red-400 transition-colors"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>

                  {t.description && (
                    <p className="text-xs text-text-muted mb-3 line-clamp-2">{t.description}</p>
                  )}

                  <div className="flex items-center gap-2 mb-3">
                    <ImageIcon className="h-4 w-4 text-text-muted" />
                    <span className="text-xs text-text-secondary">{t.asset_count} mídia(s) vinculada(s)</span>
                  </div>

                  {t.asset_count > 0 && (
                    <button
                      onClick={() => setExpandedTopic(expandedTopic === t.id ? null : t.id)}
                      className="text-[10px] text-accent hover:text-accent-warm transition-colors"
                    >
                      {expandedTopic === t.id ? "Ocultar mídias" : "Ver mídias vinculadas"}
                    </button>
                  )}

                  {expandedTopic === t.id && (
                    <TopicAssetsList topicId={t.id} onDeleted={refetch} />
                  )}

                  <p className="mt-3 text-[10px] text-text-muted">
                    As mídias são gerenciadas na aba <strong>Mídias</strong>. A geração é automática via fila de ideias.
                  </p>
                </Card>
              ))}
            </div>
          )}
        </>
      )}

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

  const params: any = {};
  if (filterKind) params.media_kind = filterKind;
  if (filterStatus) params.status = filterStatus;
  const { data, loading, refetch } = usePoll(() => api.listKidsLibraryAssets(params), 5000);

  const assets = data?.assets || [];
  const kidsUploads = uploads.filter((u) => u.kind === "kids");
  const hasActiveUploads = kidsUploads.some(
    (u) => u.status === "preparing" || u.status === "uploading" || u.status === "processing"
  );

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

  return (
    <div className="space-y-4">
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
          <label className="text-xs font-medium text-text-secondary">Tags (separadas por vírgula)</label>
          <input
            type="text"
            value={tagsInput}
            onChange={(e) => setTagsInput(e.target.value)}
            placeholder="Ex: dinossauro, natureza, verde"
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

      {/* Assets list — same pattern as content.tsx (horizontal cards) */}
      {loading && !data ? (
        <div className="flex justify-center py-12">
          <Spinner className="h-6 w-6" />
        </div>
      ) : assets.length === 0 ? (
        <EmptyState
          icon={<Film className="h-8 w-8" />}
          title="Nenhuma mídia na biblioteca"
          description="Envie imagens e vídeos para a biblioteca do canal. As mídias serão selecionadas automaticamente na geração de vídeos."
        />
      ) : (
        <div className="grid gap-3">
          {assets.map((a: any) => (
            <MediaLibraryCard key={a.id} asset={a} onDeleted={refetch} />
          ))}
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

function MediaLibraryCard({ asset, onDeleted }: { asset: any; onDeleted: () => void }) {
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
    <Card className="!p-4">
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
            {asset.tags && asset.tags.length > 0 && (
              <span className="flex items-center gap-1">
                <Tag className="h-3 w-3" /> {asset.tags.slice(0, 3).join(", ")}
                {asset.tags.length > 3 && ` +${asset.tags.length - 3}`}
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
          {isReady && !editing && (
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
          )}
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

// ── Topic Assets List ────────────────────────────────────────────────────────

function TopicAssetsList({ topicId, onDeleted }: { topicId: number; onDeleted: () => void }) {
  const { data, loading, refetch } = usePoll(() => api.listKidsAssets(topicId), 5000);
  const [deleting, setDeleting] = useState<number | null>(null);

  const assets = data?.assets || [];

  const handleDelete = async (id: number) => {
    if (!confirm("Excluir esta mídia?")) return;
    setDeleting(id);
    try {
      await api.deleteKidsAsset(id);
      toast.success("Mídia excluída");
      await refetch();
      await onDeleted();
    } catch (err: any) {
      toast.error(err.message || "Erro ao excluir");
    } finally {
      setDeleting(null);
    }
  };

  if (loading && !data) {
    return <div className="mt-2 flex justify-center"><Spinner className="h-4 w-4" /></div>;
  }

  if (assets.length === 0) {
    return <p className="mt-2 text-[10px] text-text-muted">Nenhuma mídia encontrada.</p>;
  }

  return (
    <div className="mt-3 space-y-1.5 max-h-48 overflow-y-auto">
      {assets.map((a: any) => (
        <div key={a.id} className="flex items-center gap-2 rounded-md border border-border bg-surface px-2 py-1.5">
          {/* Thumbnail or icon */}
          {a.thumbnail_key ? (
            <img
              src={api.getKidsAssetThumbnailUrl(a.thumbnail_key)}
              alt=""
              className="h-8 w-8 rounded object-cover flex-shrink-0"
            />
          ) : (
            <div className="flex-shrink-0">
              {a.media_kind === "video" ? (
                <VideoIcon className="h-4 w-4 text-text-muted" />
              ) : (
                <ImageIcon className="h-4 w-4 text-text-muted" />
              )}
            </div>
          )}

          {/* Filename + metadata */}
          <div className="flex-1 min-w-0">
            <p className="text-[11px] font-medium truncate">{a.filename}</p>
            <div className="flex items-center gap-1.5 text-[9px] text-text-muted">
              {a.media_kind === "video" && a.duration > 0 && (
                <span>{a.duration.toFixed(1)}s</span>
              )}
              {a.width > 0 && a.height > 0 && (
                <span>{a.width}×{a.height}</span>
              )}
              {a.file_size > 0 && (
                <span>{(a.file_size / 1024 / 1024).toFixed(1)}MB</span>
              )}
            </div>
          </div>

          {/* Status badge */}
          <AssetStatusBadge status={a.processing_status} error={a.process_error} />

          {/* Delete */}
          <button
            onClick={() => handleDelete(a.id)}
            disabled={deleting === a.id}
            className="text-text-muted hover:text-red-400 transition-colors flex-shrink-0"
          >
            {deleting === a.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
          </button>
        </div>
      ))}
    </div>
  );
}

function AssetStatusBadge({ status, error }: { status: string; error?: string }) {
  if (status === "ready") {
    return <CheckCircle className="h-3.5 w-3.5 text-green-400 flex-shrink-0" />;
  }
  if (status === "failed") {
    return (
      <span title={error || "Erro no processamento"} className="flex-shrink-0">
        <AlertCircle className="h-3.5 w-3.5 text-red-400" />
      </span>
    );
  }
  if (status === "processing") {
    return (
      <span title="Extraindo metadados (FFprobe + thumbnail)" className="flex-shrink-0">
        <Loader2 className="h-3.5 w-3.5 text-yellow-400 animate-spin" />
      </span>
    );
  }
  if (status === "mapping") {
    return (
      <span title="Mapeando conteúdo (VLM + ASR → eventos semânticos)" className="flex-shrink-0">
        <Loader2 className="h-3.5 w-3.5 text-blue-400 animate-spin" />
      </span>
    );
  }
  // queued or uploading
  return <Clock className="h-3.5 w-3.5 text-text-muted flex-shrink-0" />;
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
