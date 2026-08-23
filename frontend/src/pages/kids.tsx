import { useState, useRef, useEffect } from "react";
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
  Settings,
  Brain,
  Save,
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

type Tab = "topics" | "config";

export function KidsPage() {
  const [tab, setTab] = useState<Tab>("topics");
  const { data: topicsData, loading, refetch } = usePoll(() => api.listKidsTopics(), 15000);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [generating, setGenerating] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

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
              description="Crie seu primeiro tópico Kids ou vá à aba Ideias para descobrir conteúdo automaticamente."
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
        </>
      )}

      {tab === "config" && <ChannelConfigSection />}
    </div>
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
  const [automation, setAutomation] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingAuto, setSavingAuto] = useState(false);

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
    kids_age_range: "3-6",
    categories: [] as string[],
    target_duration: 45,
  });

  const [autoConfig, setAutoConfig] = useState({
    kids_queue_mode: "manual",
    kids_auto_fill_queue: true,
    kids_max_queue_size: 10,
  });

  useEffect(() => {
    Promise.all([
      api.getChannelProfile(),
      api.getAutomation(),
    ])
      .then(([p, a]) => {
        setProfile(p);
        setAutomation(a);
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
          kids_age_range: meta.kids_age_range || "3-6",
          categories: meta.categories || [],
          target_duration: meta.target_duration || 45,
        });
        const cfg = a.config || {};
        setAutoConfig({
          kids_queue_mode: cfg.kids_queue_mode || "manual",
          kids_auto_fill_queue: cfg.kids_auto_fill_queue ?? true,
          kids_max_queue_size: cfg.kids_max_queue_size || 10,
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
          kids_age_range: kidsMeta.kids_age_range,
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

  const handleSaveAuto = async () => {
    setSavingAuto(true);
    try {
      const config = { ...(automation?.config || {}) };
      config.kids_queue_mode = autoConfig.kids_queue_mode;
      config.kids_auto_fill_queue = autoConfig.kids_auto_fill_queue;
      config.kids_max_queue_size = autoConfig.kids_max_queue_size;
      if (!config.kids_idea_queue) config.kids_idea_queue = [];
      await api.updateAutomation({ config });
      toast.success("Configuração da fila salva");
    } catch (err: any) {
      toast.error(err.message || "Erro ao salvar configuração");
    } finally {
      setSavingAuto(false);
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
                value={kidsMeta.kids_age_range}
                onChange={(e) => setKidsMeta({ ...kidsMeta, kids_age_range: e.target.value })}
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
              >
                <option value="3-6">3-6 anos</option>
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

      {/* Automation Kids Config */}
      <Card>
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Settings className="h-4 w-4 text-accent" />
              Fila de Automação
            </h2>
            <p className="mt-1 text-xs text-text-muted">
              Como a fila de ideias Kids é gerenciada pela automação
            </p>
          </div>
          <Button size="sm" onClick={handleSaveAuto} disabled={savingAuto}>
            {savingAuto ? <><Spinner className="h-3.5 w-3.5" /> Salvando...</> : <><Save className="h-3.5 w-3.5" /> Salvar</>}
          </Button>
        </div>

        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Modo da fila</label>
              <select
                value={autoConfig.kids_queue_mode}
                onChange={(e) => setAutoConfig({ ...autoConfig, kids_queue_mode: e.target.value })}
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
              >
                <option value="manual">Manual (curadoria própria)</option>
                <option value="auto">Automático (preenche sozinho)</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">
                Tamanho máximo da fila: {autoConfig.kids_max_queue_size}
              </label>
              <input
                type="range"
                min={1}
                max={50}
                value={autoConfig.kids_max_queue_size}
                onChange={(e) => setAutoConfig({ ...autoConfig, kids_max_queue_size: Number(e.target.value) })}
                className="mt-2 w-full"
              />
            </div>
          </div>

          <div>
            <label className="flex items-center gap-2 text-sm text-text cursor-pointer">
              <input
                type="checkbox"
                checked={autoConfig.kids_auto_fill_queue}
                onChange={(e) => setAutoConfig({ ...autoConfig, kids_auto_fill_queue: e.target.checked })}
                className="rounded border-border"
              />
              Preencher fila automaticamente com as melhores ideias avaliadas
            </label>
          </div>
        </div>
      </Card>
    </div>
  );
}
