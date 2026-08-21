import { useState, useEffect, useRef } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { Badge, Button, Card, Label, Select, Spinner } from "@/components/ui";
import { SubtitlePreview } from "@/components/subtitle-preview";
import type { VideoCustomization } from "@/components/video-customization";
import {
  VIDEO_FORMATS,
  SUBTITLE_FONTS,
  SUBTITLE_POSITIONS,
  SUBTITLE_CASES,
  SUBTITLE_COLORS,
  TRANSITION_TYPES,
  BOX_COLORS,
} from "@/components/video-customization";
import { toast } from "sonner";
import {
  Save,
  Play,
  Pause,
  Film,
  Monitor,
  Type,
  ArrowRightLeft,
  Mic,
  Palette,
  Youtube,
  Loader2,
  Upload,
  Trash2,
  ListOrdered,
  Globe,
  AlertTriangle,
  CheckCircle2,
  X,
} from "lucide-react";

const CREATIVE_STYLES = [
  { value: "", label: "Padrão (sem estilo)" },
  { value: "humor", label: "Humor" },
  { value: "absurd", label: "Absurdo" },
  { value: "sarcastic", label: "Sarcástico" },
  { value: "storytelling", label: "Narrativa" },
  { value: "curiosity", label: "Curiosidade" },
  { value: "nostalgia", label: "Nostalgia" },
  { value: "dark_humor", label: "Humor negro" },
  { value: "high_energy", label: "Alta energia" },
];

const YOUTUBE_PRIVACY = [
  { value: "public", label: "Público" },
  { value: "unlisted", label: "Não listado" },
  { value: "private", label: "Privado" },
];

const YOUTUBE_CATEGORIES = [
  { value: "20", label: "Games" },
  { value: "22", label: "Pessoas e blogs" },
  { value: "24", label: "Entretenimento" },
  { value: "23", label: "Comédia" },
  { value: "27", label: "Educação" },
];

interface AutomationConfig extends VideoCustomization {
  game_id?: number | null;
  creative_style?: string;
  youtube_privacy?: string;
  youtube_category_id?: string;
  auto_publish?: boolean;
  // V3: Reuse policy + public gameplay fallback
  max_clip_uses?: number;
  fallback_policy?: string;
  accept_public_gameplays?: boolean;
  // V3: Queue mode + reconciliador
  queue_mode?: "manual" | "automatic";
  auto_fill_queue?: boolean;
  max_queue_size?: number;
}

function SectionTitle({ icon: Icon, title, desc }: { icon: any; title: string; desc?: string }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/10 border border-accent/20">
        <Icon className="h-4 w-4 text-accent" />
      </div>
      <div>
        <h3 className="text-sm font-semibold">{title}</h3>
        {desc && <p className="text-xs text-text-muted">{desc}</p>}
      </div>
    </div>
  );
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex items-center gap-3 cursor-pointer">
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${checked ? "bg-accent" : "bg-surface-hover"}`}
      >
        <span
          className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white transition-transform duration-200 ${checked ? "translate-x-5" : "translate-x-0"}`}
        />
      </button>
      <span className="text-sm text-text-secondary">{label}</span>
    </label>
  );
}

export function AutomationPage() {
  const { data: automation } = usePoll(() => api.getAutomation(), 30000);
  const { data: games } = usePoll(() => api.listGames(), 15000);
  const { data: voices, setData: setVoices } = usePoll(() => api.listVoices(), 15000);
  const { data: dashData } = usePoll(() => api.getDashboard(), 30000);

  const [config, setConfig] = useState<AutomationConfig>({});
  const [saving, setSaving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [uploadingVoice, setUploadingVoice] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const voiceInput = useRef<HTMLInputElement>(null);

  // Load existing config on mount
  useEffect(() => {
    if (automation && !loaded) {
      setConfig(automation.config || {});
      setLoaded(true);
    }
  }, [automation, loaded]);

  const update = (key: keyof AutomationConfig, value: any) => {
    setConfig((prev) => {
      const next = { ...prev, [key]: value };
      // Clean up empty/zero values but preserve booleans
      Object.keys(next).forEach((k) => {
        const v = (next as any)[k];
        if (typeof v === "boolean") return;
        if (v === "" || v === 0 || v === undefined || v === null) delete (next as any)[k];
      });
      return next;
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateAutomation({ config });
      toast.success("Configuração salva");
    } catch (err: any) {
      toast.error(err.message || "Erro ao salvar");
    } finally {
      setSaving(false);
    }
  };

  const handleToggleAutomation = async () => {
    setToggling(true);
    try {
      if (automation?.status === "running") {
        await api.pauseAutomation();
        toast.success("Automação pausada. O vídeo atual será concluído.");
      } else {
        await api.startAutomation();
        toast.success("Automação iniciada! Vídeos serão produzidos continuamente.");
      }
    } catch (err: any) {
      toast.error(err.message || "Erro ao alterar automação");
    } finally {
      setToggling(false);
    }
  };

  const uploadVoice = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingVoice(true);
    try {
      const r = await api.uploadVoice(file);
      toast.success(`Voz "${r.filename}" enviada`);
      const updated = await api.listVoices();
      setVoices(updated);
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setUploadingVoice(false);
      e.target.value = "";
    }
  };

  const deleteVoice = async (filename: string) => {
    if (!confirm(`Excluir a voz "${filename}"?`)) return;
    try {
      await api.deleteVoice(filename);
      toast.success(`Voz excluída`);
      const updated = await api.listVoices();
      setVoices(updated);
      if (config.voice === filename) update("voice", "");
    } catch (err: any) {
      toast.error(err.message);
    }
  };

  if (!loaded) {
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
          <h1 className="text-3xl font-bold tracking-tight">Automação</h1>
          <p className="mt-1 text-sm text-text-secondary">Configure como sua máquina produz vídeos</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleSave} disabled={saving}>
            {saving ? <><Spinner className="h-4 w-4" /> Salvando...</> : <><Save className="h-4 w-4" /> Salvar</>}
          </Button>
          <Button
            variant={automation?.status === "running" ? "danger" : "primary"}
            onClick={handleToggleAutomation}
            disabled={toggling}
          >
            {toggling ? (
              <><Spinner className="h-4 w-4" /> Aguarde...</>
            ) : automation?.status === "running" ? (
              <><Pause className="h-4 w-4" /> Pausar</>
            ) : (
              <><Play className="h-4 w-4" /> Iniciar</>
            )}
          </Button>
        </div>
      </div>

      {/* Domain Section — destructive switch */}
      <DomainSection currentDomain={dashData?.channel_domain} onResetDone={() => { window.location.reload(); }} />

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Left: settings */}
        <div className="lg:col-span-2 space-y-6">
          {/* Section 1: Conteúdo (Games-only — gameplay source selection) */}
          {dashData?.channel_domain !== "kids" && (
          <Card>
            <SectionTitle icon={Film} title="Conteúdo" desc="Qual gameplay usar como fonte" />
            <div className="space-y-4">
              <div>
                <Label>Jogo</Label>
                <Select
                  value={config.game_id ? String(config.game_id) : ""}
                  onChange={(v) => update("game_id", v ? Number(v) : null)}
                >
                  <option value="">Qualquer jogo (aleatório)</option>
                  {games?.map((g: any) => (
                    <option key={g.id} value={g.id}>{g.canonical_name}</option>
                  ))}
                </Select>
                <p className="mt-1.5 text-xs text-text-muted">
                  Escolha um jogo específico ou deixe o sistema escolher aleatoriamente
                </p>
              </div>

              {/* V3: Reuse policy */}
              <div>
                <Label>Reutilização de cenas</Label>
                <Select
                  value={String(config.max_clip_uses ?? 1)}
                  onChange={(v) => update("max_clip_uses", Number(v))}
                >
                  <option value="1">1 vez (cada trecho aparece em apenas 1 vídeo)</option>
                  <option value="2">2 vezes (permite uma reutilização)</option>
                  <option value="3">3 vezes (permite duas reutilizações)</option>
                  <option value="0">Ilimitado (sem bloqueio por quota)</option>
                </Select>
                <p className="mt-1.5 text-xs text-text-muted">
                  Máximo de vídeos em que uma mesma região de gameplay pode aparecer.
                  O histórico sempre é registrado para análise de diversidade.
                </p>
              </div>

              {/* V3: Fallback policy for public gameplays */}
              <div>
                <Label>Gameplay pública</Label>
                <Select
                  value={config.fallback_policy || (config.accept_public_gameplays ? "allow_public" : "stop")}
                  onChange={(v) => update("fallback_policy", v)}
                >
                  <option value="stop">Apenas minhas gameplays</option>
                  <option value="allow_public">Permitir gameplays públicas como fallback</option>
                </Select>
                <p className="mt-1.5 text-xs text-text-muted">
                  Quando suas gameplays se esgotarem, usar gameplays públicas de outros usuários?
                </p>
              </div>
            </div>
          </Card>
          )}

          {/* Section 2: Formato */}
          <Card>
            <SectionTitle icon={Monitor} title="Formato" desc="Dimensões e duração das cenas" />
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label>Formato da tela</Label>
                <Select
                  value={config.video_format || ""}
                  onChange={(v) => update("video_format", v)}
                >
                  <option value="">Padrão (9:16)</option>
                  {VIDEO_FORMATS.map((f) => (
                    <option key={f.value} value={f.value}>{f.label}</option>
                  ))}
                </Select>
              </div>
              <div>
                <Label>Duração de cada cena (segundos)</Label>
                <input
                  type="number"
                  min={0}
                  step={1}
                  placeholder="0 = automático"
                  value={config.scene_duration || ""}
                  onChange={(e) => update("scene_duration", Number(e.target.value))}
                  className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                />
              </div>
            </div>
            <p className="mt-2 text-xs text-text-muted">
              Ex: 10 = cenas de 10s · 7200 = uma cena longa contínua
            </p>
          </Card>

          {/* Section 3: Legenda */}
          <Card>
            <SectionTitle icon={Type} title="Legenda" desc="Estilo das legendas no vídeo" />
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label>Fonte</Label>
                <Select value={config.subtitle_font || ""} onChange={(v) => update("subtitle_font", v)}>
                  {SUBTITLE_FONTS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
                </Select>
              </div>
              <div>
                <Label>Tamanho da fonte</Label>
                <input
                  type="number"
                  min={0}
                  placeholder="0 = automático"
                  value={config.subtitle_font_size || ""}
                  onChange={(e) => update("subtitle_font_size", Number(e.target.value))}
                  className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                />
              </div>
              <div>
                <Label>Cor do texto</Label>
                <Select value={config.subtitle_color || ""} onChange={(v) => update("subtitle_color", v)}>
                  {SUBTITLE_COLORS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </Select>
              </div>
              <div>
                <Label>Cor do contorno</Label>
                <Select value={config.subtitle_outline_color || ""} onChange={(v) => update("subtitle_outline_color", v)}>
                  <option value="">Padrão (preto)</option>
                  <option value="black">Preto</option>
                  <option value="white">Branco</option>
                  <option value="red">Vermelho</option>
                </Select>
              </div>
              <div>
                <Label>Posição</Label>
                <Select value={config.subtitle_position || ""} onChange={(v) => update("subtitle_position", v)}>
                  {SUBTITLE_POSITIONS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </Select>
              </div>
              <div>
                <Label>Caixa (case)</Label>
                <Select value={config.subtitle_case || ""} onChange={(v) => update("subtitle_case", v)}>
                  {SUBTITLE_CASES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </Select>
              </div>
            </div>

            {/* Box settings */}
            <div className="mt-4 space-y-3 rounded-lg border border-border bg-surface-elevated/50 p-4">
              <Toggle
                checked={config.subtitle_box_enabled ?? false}
                onChange={(v) => update("subtitle_box_enabled", v)}
                label="Ativar fundo (box) na legenda"
              />
              {config.subtitle_box_enabled && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <Label>Cor do fundo</Label>
                    <Select value={config.subtitle_box_color || ""} onChange={(v) => update("subtitle_box_color", v)}>
                      {BOX_COLORS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                    </Select>
                  </div>
                  <div>
                    <Label>Padding do fundo</Label>
                    <input
                      type="number"
                      min={0}
                      placeholder="0 = padrão"
                      value={config.subtitle_box_padding || ""}
                      onChange={(e) => update("subtitle_box_padding", Number(e.target.value))}
                      className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                    />
                  </div>
                  <div className="sm:col-span-2">
                    <Toggle
                      checked={config.subtitle_rounded_box ?? false}
                      onChange={(v) => update("subtitle_rounded_box", v)}
                      label="Fundo arredondado (pill)"
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Stroke settings */}
            <div className="mt-3 grid gap-3 sm:grid-cols-2 rounded-lg border border-border bg-surface-elevated/50 p-4">
              <div className="sm:col-span-2 text-xs font-semibold text-text-secondary">Traço (stroke)</div>
              <div>
                <Label>Cor do traço</Label>
                <Select value={config.subtitle_stroke_color || ""} onChange={(v) => update("subtitle_stroke_color", v)}>
                  <option value="">Padrão</option>
                  <option value="black">Preto</option>
                  <option value="white">Branco</option>
                  <option value="red">Vermelho</option>
                  <option value="blue">Azul</option>
                </Select>
              </div>
              <div>
                <Label>Largura do traço</Label>
                <input
                  type="number"
                  min={0}
                  placeholder="0 = padrão"
                  value={config.subtitle_stroke_width || ""}
                  onChange={(e) => update("subtitle_stroke_width", Number(e.target.value))}
                  className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                />
              </div>
            </div>
          </Card>

          {/* Section 4: Transição */}
          <Card>
            <SectionTitle icon={ArrowRightLeft} title="Transição" desc="Transição entre cenas" />
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label>Tipo de transição</Label>
                <Select value={config.transition_type || ""} onChange={(v) => update("transition_type", v)}>
                  {TRANSITION_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </Select>
              </div>
              <div>
                <Label>Duração (segundos)</Label>
                <input
                  type="number"
                  min={0}
                  max={5}
                  step={0.1}
                  placeholder="0 = padrão (0.5s)"
                  value={config.transition_duration || ""}
                  onChange={(e) => update("transition_duration", Number(e.target.value))}
                  className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                />
              </div>
            </div>
          </Card>

          {/* Section 5: Voz */}
          <Card>
            <SectionTitle icon={Mic} title="Voz" desc="Voz da narração (TTS por clonagem)" />
            <div className="flex items-center justify-between mb-3">
              <Label>Voz selecionada</Label>
              <div className="flex gap-2">
                <input ref={voiceInput} type="file" className="hidden" onChange={uploadVoice} accept=".wav,.mp3,.ogg,.flac,.m4a" />
                <Button size="sm" variant="outline" onClick={() => voiceInput.current?.click()} disabled={uploadingVoice}>
                  {uploadingVoice ? <><Spinner className="h-3.5 w-3.5" /> Enviando...</> : <><Upload className="h-3.5 w-3.5" /> Upload voz</>}
                </Button>
              </div>
            </div>
            <Select value={config.voice || ""} onChange={(v) => update("voice", v)}>
              <option value="">Padrão do sistema</option>
              {voices?.map((v: any) => (
                <option key={v.filename} value={v.filename}>{v.filename} ({v.file_size_kb} KB)</option>
              ))}
            </Select>
            <p className="mt-1.5 text-xs text-text-muted">
              Envie um áudio curto (5-30s) da voz que o XTTS deve clonar.
            </p>
            {config.voice && (
              <button className="mt-2 flex items-center gap-1 text-xs text-red-400 hover:underline" onClick={() => deleteVoice(config.voice!)}>
                <Trash2 className="h-3 w-3" /> Excluir "{config.voice}"
              </button>
            )}
          </Card>

          {/* Section 6: Estilo */}
          <Card>
            <SectionTitle icon={Palette} title="Estilo" desc="Estilo criativo do roteiro" />
            <Select value={config.creative_style || ""} onChange={(v) => update("creative_style", v)}>
              {CREATIVE_STYLES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </Select>
            <p className="mt-1.5 text-xs text-text-muted">
              O estilo influencia o tom e humor do roteiro gerado pela IA
            </p>
          </Card>

          {/* Section 7: Fila de Produção */}
          <Card>
            <SectionTitle icon={ListOrdered} title="Fila de Produção" desc="Como a automação escolhe o próximo vídeo" />
            <div className="space-y-4">
              <div>
                <Label>Modo da fila</Label>
                <Select
                  value={config.queue_mode || "automatic"}
                  onChange={(v) => update("queue_mode", v)}
                >
                  <option value="automatic">Automático (fila + decisão editorial)</option>
                  <option value="manual">Manual (apenas fila do usuário)</option>
                </Select>
                <p className="mt-1.5 text-xs text-text-muted">
                  {config.queue_mode === "manual"
                    ? "Manual: a automação só produz vídeos das ideias que você enfileirar. Quando a fila esvazia, a produção para até você adicionar mais."
                    : "Automático: quando a fila esvazia, o sistema decide automaticamente o próximo vídeo baseado no material disponível."}
                </p>
              </div>

              {config.queue_mode !== "manual" && (
                <div className="space-y-3 rounded-lg border border-border bg-surface-elevated/50 p-4">
                  <Toggle
                    checked={config.auto_fill_queue ?? false}
                    onChange={(v) => update("auto_fill_queue", v)}
                    label="Auto-preencher fila quando vazia (reconciliador)"
                  />
                  {config.auto_fill_queue && (
                    <div>
                      <Label>Tamanho máximo da fila auto-preenchida</Label>
                      <input
                        type="number"
                        min={1}
                        max={50}
                        placeholder="10"
                        value={config.max_queue_size || ""}
                        onChange={(e) => update("max_queue_size", Number(e.target.value))}
                        className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                      />
                      <p className="mt-1.5 text-xs text-text-muted">
                        Quantas ideias fresh adicionar automaticamente (ordenadas por score editorial).
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </Card>

          {/* Section 8: YouTube */}
          <Card>
            <SectionTitle icon={Youtube} title="YouTube" desc="Configurações de publicação" />
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label>Privacidade</Label>
                <Select value={config.youtube_privacy || "unlisted"} onChange={(v) => update("youtube_privacy", v)}>
                  {YOUTUBE_PRIVACY.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </Select>
              </div>
              <div>
                <Label>Categoria</Label>
                <Select value={config.youtube_category_id || "20"} onChange={(v) => update("youtube_category_id", v)}>
                  {YOUTUBE_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </Select>
              </div>
            </div>
            <div className="mt-4">
              <Toggle
                checked={config.auto_publish ?? true}
                onChange={(v) => update("auto_publish", v)}
                label="Publicar automaticamente após a geração"
              />
            </div>
          </Card>
        </div>

        {/* Right: live preview */}
        <div className="lg:col-span-1">
          <div className="sticky top-24 space-y-4">
            <Card>
              <h3 className="text-sm font-semibold mb-4">Preview ao vivo</h3>
              <SubtitlePreview opts={config} />

              {/* Summary */}
              <div className="mt-6 space-y-2 border-t border-border pt-4">
                <p className="text-xs font-semibold text-text-muted uppercase tracking-wider">Resumo</p>
                <div className="space-y-1.5 text-xs">
                  <SummaryRow label="Formato" value={config.video_format || "9:16"} />
                  <SummaryRow label="Cena" value={config.scene_duration ? `${config.scene_duration}s` : "auto"} />
                  <SummaryRow label="Voz" value={config.voice || "padrão"} />
                  <SummaryRow label="Estilo" value={CREATIVE_STYLES.find(s => s.value === config.creative_style)?.label || "padrão"} />
                  <SummaryRow label="Transição" value={TRANSITION_TYPES.find(t => t.value === config.transition_type)?.label || "padrão"} />
                  <SummaryRow label="YouTube" value={YOUTUBE_PRIVACY.find(p => p.value === (config.youtube_privacy || "unlisted"))?.label || "Não listado"} />
                  <SummaryRow label="Fila" value={config.queue_mode === "manual" ? "Manual" : "Automática"} />
                </div>
              </div>

              <div className="mt-4 flex gap-2">
                <Button variant="outline" size="sm" className="flex-1" onClick={handleSave} disabled={saving}>
                  <Save className="h-3.5 w-3.5" /> Salvar
                </Button>
                <Button
                  variant={automation?.status === "running" ? "danger" : "primary"}
                  size="sm"
                  className="flex-1"
                  onClick={handleToggleAutomation}
                  disabled={toggling}
                >
                  {toggling ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : automation?.status === "running" ? (
                    <><Pause className="h-3.5 w-3.5" /> Pausar</>
                  ) : (
                    <><Play className="h-3.5 w-3.5" /> Iniciar</>
                  )}
                </Button>
              </div>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-text-muted">{label}</span>
      <span className="font-medium text-text-secondary">{value}</span>
    </div>
  );
}

// ── Domain Section — destructive domain switch ──────────────────────────────

const DOMAIN_LABELS: Record<string, string> = {
  games: "Games",
  kids: "Kids",
  movies: "Filmes & Séries",
  conspiracy: "Mistérios & Teorias",
  technology: "Tecnologia",
};

function DomainSection({ currentDomain, onResetDone }: { currentDomain?: string; onResetDone: () => void }) {
  const [domains, setDomains] = useState<any[]>([]);
  const [current, setCurrent] = useState<string>(currentDomain || "games");
  const [loading, setLoading] = useState(true);
  const [showConfirm, setShowConfirm] = useState(false);
  const [selectedDomain, setSelectedDomain] = useState<string>("");
  const [resetting, setResetting] = useState(false);
  const [resetSummary, setResetSummary] = useState<any>(null);

  useEffect(() => {
    api.listDomains()
      .then((res) => {
        setDomains(res.domains);
        setCurrent(res.current || currentDomain || "games");
      })
      .catch((err) => toast.error(err.message))
      .finally(() => setLoading(false));
  }, [currentDomain]);

  const handleOpenConfirm = (domain: string) => {
    if (domain === current) return;
    setSelectedDomain(domain);
    setShowConfirm(true);
    setResetSummary(null);
  };

  const handleConfirmReset = async () => {
    setResetting(true);
    setResetSummary(null);
    try {
      const result = await api.resetDomain(selectedDomain, true);
      setResetSummary(result);
      toast.success(`Domínio alterado para ${DOMAIN_LABELS[selectedDomain] || selectedDomain}`);
      // Reload after a short delay so the user sees the summary
      setTimeout(() => onResetDone(), 2000);
    } catch (err: any) {
      toast.error(err.message || "Erro ao trocar domínio");
    } finally {
      setResetting(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <div className="flex justify-center py-6"><Spinner className="h-6 w-6" /></div>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <SectionTitle
          icon={Globe}
          title="Domínio do Canal"
          desc="Tipo de conteúdo que este canal produz. A troca é destrutiva."
        />
        <div className="space-y-4">
          {/* Current domain badge */}
          <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3">
            <CheckCircle2 className="h-5 w-5 text-accent" />
            <div>
              <p className="text-xs text-text-muted">Domínio atual</p>
              <p className="text-sm font-semibold">{DOMAIN_LABELS[current] || current}</p>
            </div>
          </div>

          {/* Domain selector */}
          <div>
            <label className="mb-2 block text-xs font-medium text-text-secondary">
              Trocar para
            </label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {domains.map((d) => {
                const isActive = d.value === current;
                const isImplemented = d.implemented;
                const isDisabled = isActive || !isImplemented;
                return (
                  <button
                    key={d.value}
                    onClick={() => isImplemented && handleOpenConfirm(d.value)}
                    disabled={isDisabled}
                    className={`flex items-center justify-between rounded-lg border px-3 py-2.5 text-sm transition-all ${
                      isActive
                        ? "border-accent/40 bg-accent/10 text-accent cursor-default"
                        : !isImplemented
                        ? "border-border bg-surface text-text-muted cursor-not-allowed opacity-50"
                        : "border-border bg-surface text-text-secondary hover:border-border-bright hover:bg-surface-hover"
                    }`}
                  >
                    <span className="font-medium">{d.label}</span>
                    {isActive ? (
                      <CheckCircle2 className="h-4 w-4 text-accent" />
                    ) : !isImplemented ? (
                      <span className="text-[10px] text-text-muted">em breve</span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex items-start gap-2 rounded-lg border border-yellow-600/20 bg-yellow-600/5 px-3 py-2.5">
            <AlertTriangle className="h-4 w-4 shrink-0 text-yellow-500/80 mt-0.5" />
            <p className="text-xs text-text-muted">
              Trocar de domínio apaga todo o estado de produção do canal (mídias, jobs, conteúdo não publicado, conhecimento).
              Vídeos já publicados no YouTube não são removidos.
            </p>
          </div>
        </div>
      </Card>

      {/* Confirmation modal */}
      {showConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-fade-in"
          onClick={() => !resetting && setShowConfirm(false)}
        >
          <div
            className="relative w-full max-w-lg rounded-2xl border border-border bg-surface overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-5 w-5 text-yellow-500" />
                <h2 className="text-base font-semibold">Confirmar troca de domínio</h2>
              </div>
              {!resetting && (
                <button
                  onClick={() => setShowConfirm(false)}
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-text-muted hover:bg-surface-hover hover:text-text"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>

            {/* Body */}
            <div className="space-y-4 px-5 py-5">
              {resetSummary ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-sm text-accent">
                    <CheckCircle2 className="h-5 w-5" />
                    <span className="font-semibold">Domínio alterado com sucesso!</span>
                  </div>
                  <div className="rounded-lg border border-border bg-surface-elevated p-3 text-xs space-y-1.5">
                    <p className="text-text-muted">Resumo da limpeza:</p>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                      <span>Jobs cancelados:</span><span className="font-medium">{resetSummary.jobs_cancelled}</span>
                      <span>Vídeos deletados:</span><span className="font-medium">{resetSummary.videos_deleted}</span>
                      <span>Planos deletados:</span><span className="font-medium">{resetSummary.content_plans_deleted}</span>
                      <span>Fatos deletados:</span><span className="font-medium">{resetSummary.facts_deleted}</span>
                      <span>Documentos:</span><span className="font-medium">{resetSummary.documents_deleted}</span>
                      <span>Itens de conhecimento:</span><span className="font-medium">{resetSummary.knowledge_items_deleted}</span>
                      <span>Gameplays:</span><span className="font-medium">{resetSummary.gameplay_sources_deleted}</span>
                      <span>Jobs de limpeza:</span><span className="font-medium">{resetSummary.cleanup_jobs_created}</span>
                      <span>Vídeos preservados:</span><span className="font-medium">{resetSummary.videos_preserved_published}</span>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted">Recarregando página...</p>
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-3 rounded-lg border border-border bg-surface-elevated px-4 py-3">
                    <div className="text-center">
                      <p className="text-xs text-text-muted">De</p>
                      <p className="text-sm font-semibold">{DOMAIN_LABELS[current] || current}</p>
                    </div>
                    <div className="flex-1 text-center text-text-muted">→</div>
                    <div className="text-center">
                      <p className="text-xs text-text-muted">Para</p>
                      <p className="text-sm font-semibold text-accent">{DOMAIN_LABELS[selectedDomain] || selectedDomain}</p>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <p className="text-sm font-medium text-text-secondary">Os seguintes dados serão permanentemente removidos:</p>
                    <ul className="space-y-1.5 text-xs text-text-muted">
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> Mídias importadas e armazenadas nos workers</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> Mídias em processo de importação</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> Jobs em fila e em execução</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> Conteúdo gerado não publicado</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> Dados específicos do domínio anterior</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> Conhecimento/RAG específico do domínio</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> Estado de produção e plano editorial</li>
                    </ul>
                  </div>

                  <div className="flex items-start gap-2 rounded-lg border border-green-600/20 bg-green-600/5 px-3 py-2.5">
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500/80 mt-0.5" />
                    <p className="text-xs text-text-muted">
                      Vídeos já publicados no YouTube <strong>não</strong> serão removidos.
                      A conexão com YouTube não será alterada.
                    </p>
                  </div>

                  <div className="rounded-lg border border-red-600/30 bg-red-600/10 px-3 py-2.5">
                    <p className="text-xs text-red-400 font-medium">
                      ⚠️ Esta operação não pode ser desfeita.
                    </p>
                  </div>
                </>
              )}
            </div>

            {/* Footer */}
            {!resetSummary && (
              <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-4">
                <Button
                  variant="ghost"
                  onClick={() => setShowConfirm(false)}
                  disabled={resetting}
                >
                  Cancelar
                </Button>
                <Button
                  variant="danger"
                  onClick={handleConfirmReset}
                  disabled={resetting}
                >
                  {resetting ? (
                    <><Spinner className="h-4 w-4" /> Resetando...</>
                  ) : (
                    <><AlertTriangle className="h-4 w-4" /> Entendo e quero continuar</>
                  )}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
