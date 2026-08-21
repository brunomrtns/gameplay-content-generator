import { useState, useRef, useCallback } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { Badge, Button, Card, Spinner, EmptyState } from "@/components/ui";
import { toast } from "sonner";
import {
  Upload,
  Trash2,
  Image as ImageIcon,
  Plus,
  Sparkles,
  Loader2,
  FileText,
  X,
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

export function KidsPage() {
  const { data: topicsData, loading, refetch } = usePoll(() => api.listKidsTopics(), 15000);
  const [showCreate, setShowCreate] = useState(false);
  const [selectedTopic, setSelectedTopic] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [generating, setGenerating] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Create form state
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
    if (!confirm("Excluir este tópico e todas as suas imagens?")) return;
    try {
      await api.deleteKidsTopic(id);
      toast.success("Tópico excluído");
      if (selectedTopic === id) setSelectedTopic(null);
      await refetch();
    } catch (err: any) {
      toast.error(err.message || "Erro ao excluir");
    }
  };

  const handleUpload = async (topicId: number, files: FileList) => {
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await api.uploadKidsAsset(topicId, file);
      }
      toast.success(`${files.length} imagem(s) enviada(s)`);
      await refetch();
    } catch (err: any) {
      toast.error(err.message || "Erro no upload");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleGenerate = async (topicId: number) => {
    setGenerating(topicId);
    try {
      const result = await api.generateKidsVideo(topicId);
      toast.success(`Job #${result.job_id} criado! O vídeo será gerado.`);
    } catch (err: any) {
      toast.error(err.message || "Erro ao gerar vídeo");
    } finally {
      setGenerating(null);
    }
  };

  const handleDeleteAsset = async (assetId: number) => {
    try {
      await api.deleteKidsAsset(assetId);
      toast.success("Imagem excluída");
      await refetch();
    } catch (err: any) {
      toast.error(err.message || "Erro ao excluir imagem");
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
    <div className="space-y-8 animate-fade-in">
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
        <Button variant="primary" size="lg" onClick={() => setShowCreate(true)}>
          <Plus className="h-4 w-4" /> Novo tópico
        </Button>
      </div>

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
          description="Crie seu primeiro tópico Kids para começar a produzir vídeos educativos."
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {topics.map((t) => (
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

              {/* Asset count + upload */}
              <div className="flex items-center gap-2 mb-3">
                <ImageIcon className="h-4 w-4 text-text-muted" />
                <span className="text-xs text-text-secondary">{t.asset_count} imagem(ns)</span>
              </div>

              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                multiple
                className="hidden"
                onChange={(e) => e.target.files && handleUpload(t.id, e.target.files)}
              />

              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="flex-1"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                >
                  {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                  Imagens
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  className="flex-1"
                  onClick={() => handleGenerate(t.id)}
                  disabled={generating === t.id || t.asset_count === 0}
                >
                  {generating === t.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  Gerar
                </Button>
              </div>

              {t.asset_count === 0 && (
                <p className="mt-2 text-[10px] text-text-muted">
                  Envie imagens antes de gerar o vídeo.
                </p>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
