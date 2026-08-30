import { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { useDomain } from "@/lib/domain-config";
import { useLiveData } from "@/hooks/useLiveData";
import { useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Card, Label, Select, Spinner } from "@/components/ui";
import { SubtitlePreview } from "@/components/subtitle-preview";
import { ThumbnailPreview } from "@/components/thumbnail-preview";
import { PresentationControls, type PresentationConfig } from "@/components/presentation-controls";
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
  { value: "", labelKey: "automation:creativeStyle.default" },
  { value: "humor", labelKey: "automation:creativeStyle.humor" },
  { value: "absurd", labelKey: "automation:creativeStyle.absurd" },
  { value: "sarcastic", labelKey: "automation:creativeStyle.sarcastic" },
  { value: "storytelling", labelKey: "automation:creativeStyle.storytelling" },
  { value: "curiosity", labelKey: "automation:creativeStyle.curiosity" },
  { value: "nostalgia", labelKey: "automation:creativeStyle.nostalgia" },
  { value: "dark_humor", labelKey: "automation:creativeStyle.darkHumor" },
  { value: "high_energy", labelKey: "automation:creativeStyle.highEnergy" },
];

const YOUTUBE_PRIVACY = [
  { value: "public", labelKey: "automation:youtube.public" },
  { value: "unlisted", labelKey: "automation:youtube.unlisted" },
  { value: "private", labelKey: "automation:youtube.private" },
];

const YOUTUBE_CATEGORIES = [
  { value: "20", labelKey: "automation:youtube.categoryGames" },
  { value: "22", labelKey: "automation:youtube.categoryPeopleBlogs" },
  { value: "24", labelKey: "automation:youtube.categoryEntertainment" },
  { value: "23", labelKey: "automation:youtube.categoryComedy" },
  { value: "27", labelKey: "automation:youtube.categoryEducation" },
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
  // Kids: Queue mode + reconciliador
  kids_queue_mode?: string;
  kids_auto_fill_queue?: boolean;
  kids_max_queue_size?: number;
  // Presentation Layer: thumbnail + opening
  presentation?: PresentationConfig;
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
  const { t } = useTranslation();
  const { config: domainConfig } = useDomain();
  const queryClient = useQueryClient();
  const { data: automation } = useLiveData(['automation'], () => api.getAutomation(), ['automation.status_changed', 'job.status_changed']);
  const { data: games } = useLiveData(['games'], () => api.listGames(), ['game.enriched']);
  const { data: voices } = useLiveData(['voices'], () => api.listVoices(), []);
  const { data: dashData } = useLiveData(['dashboard'], () => api.getDashboard(), ['job.status_changed', 'video.created']);

  const [config, setConfig] = useState<AutomationConfig>({});
  const [saving, setSaving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [uploadingVoice, setUploadingVoice] = useState(false);
  const [previewMode, setPreviewMode] = useState<"video" | "capa">("video");
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
      // Clean up empty/undefined values but preserve booleans and
      // numeric 0 (used by max_clip_uses=0 to mean "unlimited").
      Object.keys(next).forEach((k) => {
        const v = (next as any)[k];
        if (typeof v === "boolean") return;
        if (typeof v === "number") return;
        if (v === "" || v === undefined || v === null) delete (next as any)[k];
      });
      return next;
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateAutomation({ config });
      toast.success(t("automation:toast.configSaved"));
    } catch (err: any) {
      toast.error(err.message || t("automation:toast.saveError"));
    } finally {
      setSaving(false);
    }
  };

  const handleToggleAutomation = async () => {
    setToggling(true);
    try {
      if (automation?.status === "running") {
        await api.pauseAutomation();
        toast.success(t("automation:toast.paused"));
      } else {
        await api.startAutomation();
        toast.success(t("automation:toast.started"));
      }
    } catch (err: any) {
      toast.error(err.message || t("automation:toast.toggleError"));
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
      toast.success(t("automation:toast.voiceUploaded", { filename: r.filename }));
      const updated = await api.listVoices();
      queryClient.setQueryData(['voices'], updated);
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setUploadingVoice(false);
      e.target.value = "";
    }
  };

  const deleteVoice = async (filename: string) => {
    if (!confirm(t("automation:toast.confirmDeleteVoice", { filename }))) return;
    try {
      await api.deleteVoice(filename);
      toast.success(t("automation:toast.voiceDeleted"));
      const updated = await api.listVoices();
      queryClient.setQueryData(['voices'], updated);
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
          <h1 className="text-3xl font-bold tracking-tight">{t("automation:header.title")}</h1>
          <p className="mt-1 text-sm text-text-secondary">{t("automation:header.subtitle")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleSave} disabled={saving}>
            {saving ? <><Spinner className="h-4 w-4" /> {t("automation:header.saving")}</> : <><Save className="h-4 w-4" /> {t("common:save")}</>}
          </Button>
          <Button
            variant={automation?.status === "running" ? "danger" : "primary"}
            onClick={handleToggleAutomation}
            disabled={toggling}
          >
            {toggling ? (
              <><Spinner className="h-4 w-4" /> {t("automation:header.pleaseWait")}</>
            ) : automation?.status === "running" ? (
              <><Pause className="h-4 w-4" /> {t("automation:header.pause")}</>
            ) : (
              <><Play className="h-4 w-4" /> {t("automation:header.start")}</>
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
          {domainConfig.features.gameplayUpload && (
          <Card>
            <SectionTitle icon={Film} title={t("automation:content.title")} desc={t("automation:content.description")} />
            <div className="space-y-4">
              <div>
                <Label>{t("common:game")}</Label>
                <Select
                  value={config.game_id ? String(config.game_id) : ""}
                  onChange={(v) => update("game_id", v ? Number(v) : null)}
                >
                  <option value="">{t("automation:content.anyGame")}</option>
                  {games?.map((g: any) => (
                    <option key={g.id} value={g.id}>{g.canonical_name}</option>
                  ))}
                </Select>
                <p className="mt-1.5 text-xs text-text-muted">
                  {t("automation:content.gameHint")}
                </p>
              </div>

              {/* V3: Reuse policy */}
              <div>
                <Label>{t("automation:content.sceneReuse")}</Label>
                <Select
                  value={String(config.max_clip_uses ?? 1)}
                  onChange={(v) => update("max_clip_uses", Number(v))}
                >
                  <option value="1">{t("automation:content.reuse1")}</option>
                  <option value="2">{t("automation:content.reuse2")}</option>
                  <option value="3">{t("automation:content.reuse3")}</option>
                  <option value="0">{t("automation:content.reuseUnlimited")}</option>
                </Select>
                <p className="mt-1.5 text-xs text-text-muted">
                  {t("automation:content.reuseHint")}
                </p>
              </div>

              {/* V3: Fallback policy for public gameplays */}
              <div>
                <Label>{t("automation:content.publicGameplay")}</Label>
                <Select
                  value={config.fallback_policy || (config.accept_public_gameplays ? "allow_public" : "stop")}
                  onChange={(v) => update("fallback_policy", v)}
                >
                  <option value="stop">{t("automation:content.onlyMine")}</option>
                  <option value="allow_public">{t("automation:content.allowPublic")}</option>
                </Select>
                <p className="mt-1.5 text-xs text-text-muted">
                  {t("automation:content.fallbackHint")}
                </p>
              </div>
            </div>
          </Card>
          )}

          {/* Section 2: Formato */}
          <Card>
            <SectionTitle icon={Monitor} title={t("automation:format.title")} desc={t("automation:format.description")} />
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label>{t("automation:format.screenFormat")}</Label>
                <Select
                  value={config.video_format || ""}
                  onChange={(v) => update("video_format", v)}
                >
                  <option value="">{t("automation:format.default")}</option>
                  {VIDEO_FORMATS.map((f) => (
                    <option key={f.value} value={f.value}>{f.label}</option>
                  ))}
                </Select>
              </div>
              <div>
                <Label>{t("automation:format.sceneDuration")}</Label>
                <input
                  type="number"
                  min={0}
                  step={1}
                  placeholder={t("automation:format.autoPlaceholder")}
                  value={config.scene_duration || ""}
                  onChange={(e) => update("scene_duration", Number(e.target.value))}
                  className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                />
              </div>
            </div>
            <p className="mt-2 text-xs text-text-muted">
              {t("automation:format.durationHint")}
            </p>
          </Card>

          {/* Section 3: Legenda */}
          <Card>
            <SectionTitle icon={Type} title={t("automation:subtitles.title")} desc={t("automation:subtitles.description")} />
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label>{t("automation:subtitles.font")}</Label>
                <Select value={config.subtitle_font || ""} onChange={(v) => update("subtitle_font", v)}>
                  {SUBTITLE_FONTS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
                </Select>
              </div>
              <div>
                <Label>{t("automation:subtitles.fontSize")}</Label>
                <input
                  type="number"
                  min={0}
                  placeholder={t("automation:subtitles.autoPlaceholder")}
                  value={config.subtitle_font_size || ""}
                  onChange={(e) => update("subtitle_font_size", Number(e.target.value))}
                  className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                />
              </div>
              <div>
                <Label>{t("automation:subtitles.textColor")}</Label>
                <Select value={config.subtitle_color || ""} onChange={(v) => update("subtitle_color", v)}>
                  {SUBTITLE_COLORS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </Select>
              </div>
              <div>
                <Label>{t("automation:subtitles.outlineColor")}</Label>
                <Select value={config.subtitle_outline_color || ""} onChange={(v) => update("subtitle_outline_color", v)}>
                  <option value="">{t("automation:subtitles.defaultBlack")}</option>
                  <option value="black">{t("automation:subtitles.black")}</option>
                  <option value="white">{t("automation:subtitles.white")}</option>
                  <option value="red">{t("automation:subtitles.red")}</option>
                </Select>
              </div>
              <div>
                <Label>{t("automation:subtitles.position")}</Label>
                <Select value={config.subtitle_position || ""} onChange={(v) => update("subtitle_position", v)}>
                  {SUBTITLE_POSITIONS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </Select>
              </div>
              <div>
                <Label>{t("automation:subtitles.case")}</Label>
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
                label={t("automation:subtitles.enableBox")}
              />
              {config.subtitle_box_enabled && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <Label>{t("automation:subtitles.boxColor")}</Label>
                    <Select value={config.subtitle_box_color || ""} onChange={(v) => update("subtitle_box_color", v)}>
                      {BOX_COLORS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                    </Select>
                  </div>
                  <div>
                    <Label>{t("automation:subtitles.boxPadding")}</Label>
                    <input
                      type="number"
                      min={0}
                      placeholder={t("automation:subtitles.defaultPlaceholder")}
                      value={config.subtitle_box_padding || ""}
                      onChange={(e) => update("subtitle_box_padding", Number(e.target.value))}
                      className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                    />
                  </div>
                  <div className="sm:col-span-2">
                    <Toggle
                      checked={config.subtitle_rounded_box ?? false}
                      onChange={(v) => update("subtitle_rounded_box", v)}
                      label={t("automation:subtitles.roundedBox")}
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Stroke settings */}
            <div className="mt-3 grid gap-3 sm:grid-cols-2 rounded-lg border border-border bg-surface-elevated/50 p-4">
              <div className="sm:col-span-2 text-xs font-semibold text-text-secondary">{t("automation:subtitles.stroke")}</div>
              <div>
                <Label>{t("automation:subtitles.strokeColor")}</Label>
                <Select value={config.subtitle_stroke_color || ""} onChange={(v) => update("subtitle_stroke_color", v)}>
                  <option value="">{t("automation:subtitles.default")}</option>
                  <option value="black">{t("automation:subtitles.black")}</option>
                  <option value="white">{t("automation:subtitles.white")}</option>
                  <option value="red">{t("automation:subtitles.red")}</option>
                  <option value="blue">{t("automation:subtitles.blue")}</option>
                </Select>
              </div>
              <div>
                <Label>{t("automation:subtitles.strokeWidth")}</Label>
                <input
                  type="number"
                  min={0}
                  placeholder={t("automation:subtitles.defaultPlaceholder")}
                  value={config.subtitle_stroke_width || ""}
                  onChange={(e) => update("subtitle_stroke_width", Number(e.target.value))}
                  className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                />
              </div>
            </div>
          </Card>

          {/* Section 4: Transição */}
          <Card>
            <SectionTitle icon={ArrowRightLeft} title={t("automation:transitions.title")} desc={t("automation:transitions.description")} />
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label>{t("automation:transitions.type")}</Label>
                <Select value={config.transition_type || ""} onChange={(v) => update("transition_type", v)}>
                  {TRANSITION_TYPES.map((tr) => <option key={tr.value} value={tr.value}>{tr.label}</option>)}
                </Select>
              </div>
              <div>
                <Label>{t("automation:transitions.duration")}</Label>
                <input
                  type="number"
                  min={0}
                  max={5}
                  step={0.1}
                  placeholder={t("automation:transitions.durationPlaceholder")}
                  value={config.transition_duration || ""}
                  onChange={(e) => update("transition_duration", Number(e.target.value))}
                  className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                />
              </div>
            </div>
          </Card>

          {/* Section 5: Voz */}
          <Card>
            <SectionTitle icon={Mic} title={t("automation:voice.title")} desc={t("automation:voice.description")} />
            <div className="flex items-center justify-between mb-3">
              <Label>{t("automation:voice.selected")}</Label>
              <div className="flex gap-2">
                <input ref={voiceInput} type="file" className="hidden" onChange={uploadVoice} accept=".wav,.mp3,.ogg,.flac,.m4a" />
                <Button size="sm" variant="outline" onClick={() => voiceInput.current?.click()} disabled={uploadingVoice}>
                  {uploadingVoice ? <><Spinner className="h-3.5 w-3.5" /> {t("automation:voice.uploading")}</> : <><Upload className="h-3.5 w-3.5" /> {t("automation:voice.upload")}</>}
                </Button>
              </div>
            </div>
            <Select value={config.voice || ""} onChange={(v) => update("voice", v)}>
              <option value="">{t("automation:voice.systemDefault")}</option>
              {voices?.map((v: any) => (
                <option key={v.filename} value={v.filename}>{v.filename} ({v.file_size_kb} KB)</option>
              ))}
            </Select>
            <p className="mt-1.5 text-xs text-text-muted">
              {t("automation:voice.hint")}
            </p>
            {config.voice && (
              <button className="mt-2 flex items-center gap-1 text-xs text-red-400 hover:underline" onClick={() => deleteVoice(config.voice!)}>
                <Trash2 className="h-3 w-3" /> {t("common:delete")} "{config.voice}"
              </button>
            )}
          </Card>

          {/* Section 6: Estilo */}
          <Card>
            <SectionTitle icon={Palette} title={t("automation:style.title")} desc={t("automation:style.description")} />
            <Select value={config.creative_style || ""} onChange={(v) => update("creative_style", v)}>
              {CREATIVE_STYLES.map((s) => <option key={s.value} value={s.value}>{t(s.labelKey)}</option>)}
            </Select>
            <p className="mt-1.5 text-xs text-text-muted">
              {t("automation:style.hint")}
            </p>
          </Card>

          {/* Section 6b: Apresentação (Presentation Layer) */}
          <PresentationControls
            config={config.presentation}
            update={update}
          />

          {/* Section 7: Fila de Produção (Games) */}
          {domainConfig.features.gameplayUpload && (
          <Card>
            <SectionTitle icon={ListOrdered} title={t("automation:queue.title")} desc={t("automation:queue.description")} />
            <div className="space-y-4">
              <div>
                <Label>{t("automation:queue.mode")}</Label>
                <Select
                  value={config.queue_mode || "automatic"}
                  onChange={(v) => update("queue_mode", v)}
                >
                  <option value="automatic">{t("automation:queue.automatic")}</option>
                  <option value="manual">{t("automation:queue.manual")}</option>
                </Select>
                <p className="mt-1.5 text-xs text-text-muted">
                  {config.queue_mode === "manual"
                    ? t("automation:queue.manualDesc")
                    : t("automation:queue.automaticDesc")}
                </p>
              </div>

              {config.queue_mode !== "manual" && (
                <div className="space-y-3 rounded-lg border border-border bg-surface-elevated/50 p-4">
                  <Toggle
                    checked={config.auto_fill_queue ?? false}
                    onChange={(v) => update("auto_fill_queue", v)}
                    label={t("automation:queue.autoFill")}
                  />
                  {config.auto_fill_queue && (
                    <div>
                      <Label>{t("automation:queue.maxSize")}</Label>
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
                        {t("automation:queue.maxSizeHint")}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </Card>
          )}

          {/* Section 7b: Fila de Produção (Kids) */}
          {domainConfig.id === "kids" && (
          <Card>
            <SectionTitle icon={ListOrdered} title={t("automation:queue.title")} desc={t("automation:queue.kidsDescription")} />
            <div className="space-y-4">
              <div>
                <Label>{t("automation:queue.mode")}</Label>
                <Select
                  value={config.kids_queue_mode || "manual"}
                  onChange={(v) => update("kids_queue_mode", v)}
                >
                  <option value="manual">{t("automation:queue.manual")}</option>
                  <option value="auto">{t("automation:queue.kidsAutomatic")}</option>
                </Select>
                <p className="mt-1.5 text-xs text-text-muted">
                  {config.kids_queue_mode === "auto"
                    ? t("automation:queue.kidsAutoDesc")
                    : t("automation:queue.kidsManualDesc")}
                </p>
              </div>

              <div className="space-y-3 rounded-lg border border-border bg-surface-elevated/50 p-4">
                <Toggle
                  checked={config.kids_auto_fill_queue ?? true}
                  onChange={(v) => update("kids_auto_fill_queue", v)}
                  label={t("automation:queue.kidsAutoFill")}
                />
                <div>
                  <Label>{t("automation:queue.kidsMaxSize")}</Label>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    placeholder="10"
                    value={config.kids_max_queue_size || 10}
                    onChange={(e) => update("kids_max_queue_size", Number(e.target.value))}
                    className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted backdrop-blur-sm transition-all"
                  />
                  <p className="mt-1.5 text-xs text-text-muted">
                    {t("automation:queue.kidsMaxSizeHint")}
                  </p>
                </div>
              </div>
            </div>
          </Card>
          )}

          {/* Section 8: YouTube */}
          <Card>
            <SectionTitle icon={Youtube} title={t("automation:youtube.title")} desc={t("automation:youtube.description")} />
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label>{t("automation:youtube.privacy")}</Label>
                <Select value={config.youtube_privacy || "unlisted"} onChange={(v) => update("youtube_privacy", v)}>
                  {YOUTUBE_PRIVACY.map((p) => <option key={p.value} value={p.value}>{t(p.labelKey)}</option>)}
                </Select>
              </div>
              <div>
                <Label>{t("automation:youtube.category")}</Label>
                <Select value={config.youtube_category_id || "20"} onChange={(v) => update("youtube_category_id", v)}>
                  {YOUTUBE_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{t(c.labelKey)}</option>)}
                </Select>
              </div>
            </div>
            <div className="mt-4">
              <Toggle
                checked={config.auto_publish ?? true}
                onChange={(v) => update("auto_publish", v)}
                label={t("automation:youtube.autoPublish")}
              />
            </div>
          </Card>
        </div>

        {/* Right: live preview */}
        <div className="lg:col-span-1">
          <div className="sticky top-24 space-y-4">
            <Card>
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold">{t("automation:preview.livePreview")}</h3>
                {config.presentation?.enabled && (
                  <div className="flex gap-1 rounded-lg bg-bg/60 p-0.5">
                    <button
                      className={`px-2.5 py-1 text-xs rounded-md transition-all ${previewMode === "video" ? "bg-teal-500 text-white" : "text-text-muted"}`}
                      onClick={() => setPreviewMode("video")}
                    >
                      {t("automation:preview.video")}
                    </button>
                    <button
                      className={`px-2.5 py-1 text-xs rounded-md transition-all ${previewMode === "capa" ? "bg-teal-500 text-white" : "text-text-muted"}`}
                      onClick={() => setPreviewMode("capa")}
                    >
                      {t("automation:preview.cover")}
                    </button>
                  </div>
                )}
              </div>
              {previewMode === "capa" && config.presentation?.enabled ? (
                <ThumbnailPreview
                  opts={{
                    video_format: config.video_format,
                    thumbnail_text_enabled: config.presentation.thumbnail_text_enabled,
                    thumbnail_text_source: config.presentation.thumbnail_text_source,
                    thumbnail_text_custom: config.presentation.thumbnail_text_custom,
                    thumbnail_text_position: config.presentation.thumbnail_text_position,
                    thumbnail_text_color: config.presentation.thumbnail_text_color,
                    thumbnail_text_size: config.presentation.thumbnail_text_size,
                    thumbnail_image_path: config.presentation.thumbnail_image_path,
                  }}
                />
              ) : (
                <SubtitlePreview opts={config} />
              )}

              {/* Summary */}
              <div className="mt-6 space-y-2 border-t border-border pt-4">
                <p className="text-xs font-semibold text-text-muted uppercase tracking-wider">{t("automation:preview.summary")}</p>
                <div className="space-y-1.5 text-xs">
                  <SummaryRow label={t("automation:preview.format")} value={config.video_format || "9:16"} />
                  <SummaryRow label={t("automation:preview.scene")} value={config.scene_duration ? `${config.scene_duration}s` : t("automation:preview.default")} />
                  <SummaryRow label={t("automation:preview.voice")} value={config.voice || t("automation:preview.default")} />
                  <SummaryRow label={t("automation:preview.style")} value={CREATIVE_STYLES.find(s => s.value === config.creative_style)?.labelKey ? t(CREATIVE_STYLES.find(s => s.value === config.creative_style)!.labelKey) : t("automation:preview.default")} />
                  <SummaryRow label={t("automation:preview.transition")} value={TRANSITION_TYPES.find(tr => tr.value === config.transition_type)?.label || t("automation:preview.default")} />
                  <SummaryRow label={t("automation:youtube.title")} value={YOUTUBE_PRIVACY.find(p => p.value === (config.youtube_privacy || "unlisted"))?.labelKey ? t(YOUTUBE_PRIVACY.find(p => p.value === (config.youtube_privacy || "unlisted"))!.labelKey) : t("automation:youtube.unlisted")} />
                  <SummaryRow label={t("automation:preview.queue")} value={config.queue_mode === "manual" ? t("automation:preview.manual") : t("automation:preview.automatic")} />
                </div>
              </div>

              <div className="mt-4 flex gap-2">
                <Button variant="outline" size="sm" className="flex-1" onClick={handleSave} disabled={saving}>
                  <Save className="h-3.5 w-3.5" /> {t("common:save")}
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
                    <><Pause className="h-3.5 w-3.5" /> {t("automation:header.pause")}</>
                  ) : (
                    <><Play className="h-3.5 w-3.5" /> {t("automation:header.start")}</>
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

const DOMAIN_LABEL_KEYS: Record<string, string> = {
  games: "automation:domain.games",
  kids: "automation:domain.kids",
  movies: "automation:domain.movies",
  conspiracy: "automation:domain.conspiracy",
  technology: "automation:domain.technology",
};

function DomainSection({ currentDomain, onResetDone }: { currentDomain?: string; onResetDone: () => void }) {
  const { t } = useTranslation();
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
      toast.success(t("automation:domain.changedTo", { domain: DOMAIN_LABEL_KEYS[selectedDomain] ? t(DOMAIN_LABEL_KEYS[selectedDomain]) : selectedDomain }));
      // Reload after a short delay so the user sees the summary
      setTimeout(() => onResetDone(), 2000);
    } catch (err: any) {
      toast.error(err.message || t("automation:domain.changeError"));
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
          title={t("automation:domain.title")}
          desc={t("automation:domain.description")}
        />
        <div className="space-y-4">
          {/* Current domain badge */}
          <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3">
            <CheckCircle2 className="h-5 w-5 text-accent" />
            <div>
              <p className="text-xs text-text-muted">{t("automation:domain.current")}</p>
              <p className="text-sm font-semibold">{DOMAIN_LABEL_KEYS[current] ? t(DOMAIN_LABEL_KEYS[current]) : current}</p>
            </div>
          </div>

          {/* Domain selector */}
          <div>
            <label className="mb-2 block text-xs font-medium text-text-secondary">
              {t("automation:domain.switchTo")}
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
                      <span className="text-[10px] text-text-muted">{t("automation:domain.comingSoon")}</span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex items-start gap-2 rounded-lg border border-yellow-600/20 bg-yellow-600/5 px-3 py-2.5">
            <AlertTriangle className="h-4 w-4 shrink-0 text-yellow-500/80 mt-0.5" />
            <p className="text-xs text-text-muted">
              {t("automation:domain.warning")}
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
                <h2 className="text-base font-semibold">{t("automation:domain.confirmTitle")}</h2>
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
                    <span className="font-semibold">{t("automation:domain.changeSuccess")}</span>
                  </div>
                  <div className="rounded-lg border border-border bg-surface-elevated p-3 text-xs space-y-1.5">
                    <p className="text-text-muted">{t("automation:domain.cleanupSummary")}</p>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                      <span>{t("automation:domain.jobsCancelled")}</span><span className="font-medium">{resetSummary.jobs_cancelled}</span>
                      <span>{t("automation:domain.videosDeleted")}</span><span className="font-medium">{resetSummary.videos_deleted}</span>
                      <span>{t("automation:domain.plansDeleted")}</span><span className="font-medium">{resetSummary.content_plans_deleted}</span>
                      <span>{t("automation:domain.factsDeleted")}</span><span className="font-medium">{resetSummary.facts_deleted}</span>
                      <span>{t("automation:domain.documents")}</span><span className="font-medium">{resetSummary.documents_deleted}</span>
                      <span>{t("automation:domain.knowledgeItems")}</span><span className="font-medium">{resetSummary.knowledge_items_deleted}</span>
                      <span>{t("automation:domain.gameplays")}</span><span className="font-medium">{resetSummary.gameplay_sources_deleted}</span>
                      <span>{t("automation:domain.cleanupJobs")}</span><span className="font-medium">{resetSummary.cleanup_jobs_created}</span>
                      <span>{t("automation:domain.videosPreserved")}</span><span className="font-medium">{resetSummary.videos_preserved_published}</span>
                    </div>
                  </div>
                  <p className="text-xs text-text-muted">{t("automation:domain.reloading")}</p>
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-3 rounded-lg border border-border bg-surface-elevated px-4 py-3">
                    <div className="text-center">
                      <p className="text-xs text-text-muted">{t("automation:domain.from")}</p>
                      <p className="text-sm font-semibold">{DOMAIN_LABEL_KEYS[current] ? t(DOMAIN_LABEL_KEYS[current]) : current}</p>
                    </div>
                    <div className="flex-1 text-center text-text-muted">→</div>
                    <div className="text-center">
                      <p className="text-xs text-text-muted">{t("automation:domain.to")}</p>
                      <p className="text-sm font-semibold text-accent">{DOMAIN_LABEL_KEYS[selectedDomain] ? t(DOMAIN_LABEL_KEYS[selectedDomain]) : selectedDomain}</p>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <p className="text-sm font-medium text-text-secondary">{t("automation:domain.willBeRemoved")}</p>
                    <ul className="space-y-1.5 text-xs text-text-muted">
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> {t("automation:domain.removed.importedMedia")}</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> {t("automation:domain.removed.importingMedia")}</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> {t("automation:domain.removed.queuedJobs")}</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> {t("automation:domain.removed.unpublishedContent")}</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> {t("automation:domain.removed.domainData")}</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> {t("automation:domain.removed.domainKnowledge")}</li>
                      <li className="flex items-center gap-2"><Trash2 className="h-3 w-3 text-red-400/70" /> {t("automation:domain.removed.productionState")}</li>
                    </ul>
                  </div>

                  <div className="flex items-start gap-2 rounded-lg border border-green-600/20 bg-green-600/5 px-3 py-2.5">
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500/80 mt-0.5" />
                    <p className="text-xs text-text-muted">
                      {t("automation:domain.preservedNote")}
                    </p>
                  </div>

                  <div className="rounded-lg border border-red-600/30 bg-red-600/10 px-3 py-2.5">
                    <p className="text-xs text-red-400 font-medium">
                      {t("automation:domain.irreversible")}
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
                  {t("common:cancel")}
                </Button>
                <Button
                  variant="danger"
                  onClick={handleConfirmReset}
                  disabled={resetting}
                >
                  {resetting ? (
                    <><Spinner className="h-4 w-4" /> {t("automation:domain.resetting")}</>
                  ) : (
                    <><AlertTriangle className="h-4 w-4" /> {t("automation:domain.confirmContinue")}</>
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
