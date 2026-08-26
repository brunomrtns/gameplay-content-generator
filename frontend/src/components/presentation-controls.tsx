import { useRef, useState } from "react";
import { Upload, Trash2, Loader2, ImageIcon } from "lucide-react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Card, Select, Label, Button, Spinner } from "@/components/ui";

export interface PresentationConfig {
  enabled: boolean;
  // Thumbnail
  thumbnail_enabled: boolean;
  thumbnail_mode: "auto" | "imported" | "fixed";
  thumbnail_image_path?: string;
  thumbnail_text_enabled: boolean;
  thumbnail_text_source: "title" | "custom";
  thumbnail_text_custom?: string;
  thumbnail_text_position: "top" | "middle" | "bottom";
  thumbnail_text_color: string;
  thumbnail_text_outline: string;
  thumbnail_text_size: "medium" | "large" | "xlarge";
  // Opening
  opening_enabled: boolean;
  opening_duration: number;
  opening_image_mode: "same_as_thumbnail" | "auto" | "imported" | "fixed";
  opening_image_path?: string;
  opening_text_enabled: boolean;
  opening_text_source: "title" | "hook" | "custom";
  opening_text_custom?: string;
  opening_text_position: "top" | "middle" | "bottom";
  opening_text_color: string;
  opening_text_outline: string;
  opening_text_size: "medium" | "large" | "xlarge";
  opening_narration_enabled: boolean;
  opening_narration_text?: string;
  // Auto
  auto_candidate_count: number;
  auto_min_interesting: number;
  auto_min_confidence: number;
}

const DEFAULT_CONFIG: PresentationConfig = {
  enabled: false,
  thumbnail_enabled: true,
  thumbnail_mode: "auto",
  thumbnail_text_enabled: true,
  thumbnail_text_source: "title",
  thumbnail_text_position: "bottom",
  thumbnail_text_color: "white",
  thumbnail_text_outline: "black",
  thumbnail_text_size: "large",
  opening_enabled: true,
  opening_duration: 2.5,
  opening_image_mode: "same_as_thumbnail",
  opening_text_enabled: true,
  opening_text_source: "title",
  opening_text_position: "middle",
  opening_text_color: "white",
  opening_text_outline: "black",
  opening_text_size: "xlarge",
  opening_narration_enabled: false,
  auto_candidate_count: 5,
  auto_min_interesting: 0.4,
  auto_min_confidence: 0.5,
};

const COLORS = [
  { value: "white", label: "Branco" },
  { value: "yellow", label: "Amarelo" },
  { value: "cyan", label: "Ciano" },
  { value: "red", label: "Vermelho" },
  { value: "lime", label: "Verde limão" },
  { value: "black", label: "Preto" },
];

const SIZES = [
  { value: "medium", label: "Médio" },
  { value: "large", label: "Grande" },
  { value: "xlarge", label: "Extra grande" },
];

const POSITIONS = [
  { value: "top", label: "Topo" },
  { value: "middle", label: "Meio" },
  { value: "bottom", label: "Baixo" },
];

function SectionTitle({ icon: Icon, title, desc }: { icon: any; title: string; desc?: string }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-teal-500/10 text-teal-400">
        <Icon className="h-4.5 w-4.5" />
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

export function PresentationControls({
  config,
  update,
}: {
  config: PresentationConfig | undefined;
  update: (key: any, value: any) => void;
}) {
  const imageInput = useRef<HTMLInputElement>(null);
  const openingImageInput = useRef<HTMLInputElement>(null);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [uploadingOpening, setUploadingOpening] = useState(false);

  const cfg = config || DEFAULT_CONFIG;
  const updateP = (key: keyof PresentationConfig, value: any) =>
    update("presentation", { ...cfg, [key]: value });

  const uploadImage = async (file: File, target: "thumbnail" | "opening") => {
    const setter = target === "thumbnail" ? setUploadingImage : setUploadingOpening;
    setter(true);
    try {
      const res = await api.uploadPresentationImage(file);
      const url = api.presentationImageUrl(res.storage_key);
      if (target === "thumbnail") {
        updateP("thumbnail_image_path", url);
      } else {
        updateP("opening_image_path", url);
      }
      toast.success("Imagem enviada");
    } catch (err: any) {
      toast.error(err.message || "Erro ao enviar imagem");
    } finally {
      setter(false);
    }
  };

  return (
    <Card>
      <SectionTitle
        icon={ImageIcon}
        title="Apresentação"
        desc="Capa do vídeo + abertura visual (opcional)"
      />

      <Toggle
        checked={cfg.enabled ?? false}
        onChange={(v) => updateP("enabled", v)}
        label="Ativar camada de apresentação"
      />

      {cfg.enabled && (
        <div className="mt-4 space-y-6">
          {/* ── Thumbnail / Capa ── */}
          <div className="space-y-3 rounded-lg border border-border bg-surface-elevated/50 p-4">
            <p className="text-xs font-semibold text-text-muted uppercase tracking-wider">
              Capa / Thumbnail
            </p>

            <Toggle
              checked={cfg.thumbnail_enabled ?? true}
              onChange={(v) => updateP("thumbnail_enabled", v)}
              label="Gerar capa personalizada"
            />

            {cfg.thumbnail_enabled && (
              <>
                <div>
                  <Label>Modo de seleção</Label>
                  <Select
                    value={cfg.thumbnail_mode || "auto"}
                    onChange={(v) => updateP("thumbnail_mode", v)}
                  >
                    <option value="auto">Automático (frame do gameplay)</option>
                    <option value="imported">Imagem importada (por vídeo)</option>
                    <option value="fixed">Imagem fixa (para todos os vídeos)</option>
                  </Select>
                </div>

                {(cfg.thumbnail_mode === "imported" || cfg.thumbnail_mode === "fixed") && (
                  <div>
                    <Label>Imagem</Label>
                    <div className="flex gap-2">
                      <input
                        ref={imageInput}
                        type="file"
                        className="hidden"
                        accept="image/*"
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          if (f) uploadImage(f, "thumbnail");
                        }}
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => imageInput.current?.click()}
                        disabled={uploadingImage}
                      >
                        {uploadingImage ? (
                          <><Spinner className="h-3.5 w-3.5" /> Enviando...</>
                        ) : (
                          <><Upload className="h-3.5 w-3.5" /> Upload imagem</>
                        )}
                      </Button>
                      {cfg.thumbnail_image_path && (
                        <button
                          className="flex items-center gap-1 text-xs text-red-400 hover:underline"
                          onClick={() => updateP("thumbnail_image_path", "")}
                        >
                          <Trash2 className="h-3 w-3" /> Remover
                        </button>
                      )}
                    </div>
                    {cfg.thumbnail_image_path && (
                      <img
                        src={cfg.thumbnail_image_path}
                        alt="Capa"
                        className="mt-2 max-h-32 rounded-lg border border-border"
                      />
                    )}
                  </div>
                )}

                <Toggle
                  checked={cfg.thumbnail_text_enabled ?? true}
                  onChange={(v) => updateP("thumbnail_text_enabled", v)}
                  label="Texto na capa"
                />

                {cfg.thumbnail_text_enabled && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <Label>Fonte do texto</Label>
                      <Select
                        value={cfg.thumbnail_text_source || "title"}
                        onChange={(v) => updateP("thumbnail_text_source", v)}
                      >
                        <option value="title">Título do vídeo</option>
                        <option value="custom">Texto personalizado</option>
                      </Select>
                    </div>
                    {cfg.thumbnail_text_source === "custom" && (
                      <div className="sm:col-span-2">
                        <Label>Texto personalizado</Label>
                        <input
                          type="text"
                          value={cfg.thumbnail_text_custom || ""}
                          onChange={(e) => updateP("thumbnail_text_custom", e.target.value)}
                          placeholder="Digite o texto da capa"
                          className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text placeholder:text-text-muted"
                        />
                      </div>
                    )}
                    <div>
                      <Label>Posição</Label>
                      <Select
                        value={cfg.thumbnail_text_position || "bottom"}
                        onChange={(v) => updateP("thumbnail_text_position", v)}
                      >
                        {POSITIONS.map((p) => (
                          <option key={p.value} value={p.value}>{p.label}</option>
                        ))}
                      </Select>
                    </div>
                    <div>
                      <Label>Tamanho</Label>
                      <Select
                        value={cfg.thumbnail_text_size || "large"}
                        onChange={(v) => updateP("thumbnail_text_size", v)}
                      >
                        {SIZES.map((s) => (
                          <option key={s.value} value={s.value}>{s.label}</option>
                        ))}
                      </Select>
                    </div>
                    <div>
                      <Label>Cor do texto</Label>
                      <Select
                        value={cfg.thumbnail_text_color || "white"}
                        onChange={(v) => updateP("thumbnail_text_color", v)}
                      >
                        {COLORS.map((c) => (
                          <option key={c.value} value={c.value}>{c.label}</option>
                        ))}
                      </Select>
                    </div>
                    <div>
                      <Label>Contorno</Label>
                      <Select
                        value={cfg.thumbnail_text_outline || "black"}
                        onChange={(v) => updateP("thumbnail_text_outline", v)}
                      >
                        {COLORS.map((c) => (
                          <option key={c.value} value={c.value}>{c.label}</option>
                        ))}
                      </Select>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          {/* ── Opening / Introdução ── */}
          <div className="space-y-3 rounded-lg border border-border bg-surface-elevated/50 p-4">
            <p className="text-xs font-semibold text-text-muted uppercase tracking-wider">
              Abertura / Introdução Visual
            </p>

            <Toggle
              checked={cfg.opening_enabled ?? true}
              onChange={(v) => updateP("opening_enabled", v)}
              label="Adicionar abertura visual no início"
            />

            {cfg.opening_enabled && (
              <>
                <div>
                  <Label>Duração (segundos)</Label>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    step={0.5}
                    value={cfg.opening_duration || 2.5}
                    onChange={(e) => updateP("opening_duration", Number(e.target.value))}
                    className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text"
                  />
                </div>

                <div>
                  <Label>Imagem da abertura</Label>
                  <Select
                    value={cfg.opening_image_mode || "same_as_thumbnail"}
                    onChange={(v) => updateP("opening_image_mode", v)}
                  >
                    <option value="same_as_thumbnail">Mesma imagem da capa</option>
                    <option value="auto">Automático (frame do gameplay)</option>
                    <option value="imported">Imagem importada</option>
                    <option value="fixed">Imagem fixa</option>
                  </Select>
                </div>

                {(cfg.opening_image_mode === "imported" || cfg.opening_image_mode === "fixed") && (
                  <div>
                    <Label>Imagem da abertura</Label>
                    <div className="flex gap-2">
                      <input
                        ref={openingImageInput}
                        type="file"
                        className="hidden"
                        accept="image/*"
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          if (f) uploadImage(f, "opening");
                        }}
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => openingImageInput.current?.click()}
                        disabled={uploadingOpening}
                      >
                        {uploadingOpening ? (
                          <><Spinner className="h-3.5 w-3.5" /> Enviando...</>
                        ) : (
                          <><Upload className="h-3.5 w-3.5" /> Upload imagem</>
                        )}
                      </Button>
                      {cfg.opening_image_path && (
                        <button
                          className="flex items-center gap-1 text-xs text-red-400 hover:underline"
                          onClick={() => updateP("opening_image_path", "")}
                        >
                          <Trash2 className="h-3 w-3" /> Remover
                        </button>
                      )}
                    </div>
                  </div>
                )}

                <Toggle
                  checked={cfg.opening_text_enabled ?? true}
                  onChange={(v) => updateP("opening_text_enabled", v)}
                  label="Texto na abertura"
                />

                {cfg.opening_text_enabled && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <Label>Fonte do texto</Label>
                      <Select
                        value={cfg.opening_text_source || "title"}
                        onChange={(v) => updateP("opening_text_source", v)}
                      >
                        <option value="title">Título do vídeo</option>
                        <option value="hook">Primeira frase do roteiro</option>
                        <option value="custom">Texto personalizado</option>
                      </Select>
                    </div>
                    {cfg.opening_text_source === "custom" && (
                      <div className="sm:col-span-2">
                        <Label>Texto personalizado</Label>
                        <input
                          type="text"
                          value={cfg.opening_text_custom || ""}
                          onChange={(e) => updateP("opening_text_custom", e.target.value)}
                          placeholder="Digite o texto da abertura"
                          className="h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text"
                        />
                      </div>
                    )}
                    <div>
                      <Label>Posição</Label>
                      <Select
                        value={cfg.opening_text_position || "middle"}
                        onChange={(v) => updateP("opening_text_position", v)}
                      >
                        {POSITIONS.map((p) => (
                          <option key={p.value} value={p.value}>{p.label}</option>
                        ))}
                      </Select>
                    </div>
                    <div>
                      <Label>Tamanho</Label>
                      <Select
                        value={cfg.opening_text_size || "xlarge"}
                        onChange={(v) => updateP("opening_text_size", v)}
                      >
                        {SIZES.map((s) => (
                          <option key={s.value} value={s.value}>{s.label}</option>
                        ))}
                      </Select>
                    </div>
                    <div>
                      <Label>Cor do texto</Label>
                      <Select
                        value={cfg.opening_text_color || "white"}
                        onChange={(v) => updateP("opening_text_color", v)}
                      >
                        {COLORS.map((c) => (
                          <option key={c.value} value={c.value}>{c.label}</option>
                        ))}
                      </Select>
                    </div>
                    <div>
                      <Label>Contorno</Label>
                      <Select
                        value={cfg.opening_text_outline || "black"}
                        onChange={(v) => updateP("opening_text_outline", v)}
                      >
                        {COLORS.map((c) => (
                          <option key={c.value} value={c.value}>{c.label}</option>
                        ))}
                      </Select>
                    </div>
                  </div>
                )}

                <Toggle
                  checked={cfg.opening_narration_enabled ?? false}
                  onChange={(v) => updateP("opening_narration_enabled", v)}
                  label="Narração na abertura (TTS do título)"
                />
              </>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
