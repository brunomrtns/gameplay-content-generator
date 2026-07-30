import { useState } from "react";
import { Label } from "@/components/ui";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { toast } from "sonner";
import { SubtitlePreview } from "@/components/subtitle-preview";

export interface VideoCustomization {
  scene_duration?: number;
  video_format?: string;
  subtitle_font?: string;
  subtitle_font_size?: number;
  subtitle_color?: string;
  subtitle_outline_color?: string;
  subtitle_position?: string;
  subtitle_case?: string;
  voice?: string;
  transition_type?: string;
  transition_duration?: number;
  subtitle_box_enabled?: boolean;
  subtitle_box_color?: string;
  subtitle_box_padding?: number;
  subtitle_stroke_color?: string;
  subtitle_stroke_width?: number;
  subtitle_rounded_box?: boolean;
}

export const VIDEO_FORMATS = [
  { value: "9:16", label: "9:16 Vertical (Shorts/TikTok)" },
  { value: "16:9", label: "16:9 Horizontal (YouTube)" },
  { value: "1:1", label: "1:1 Quadrado (Instagram)" },
  { value: "4:5", label: "4:5 Retrato (Reels)" },
];

export const SUBTITLE_FONTS = [
  { value: "", label: "Padrão do perfil" },
  { value: "DejaVuSans-Bold", label: "DejaVu Sans Bold" },
  { value: "DejaVuSans", label: "DejaVu Sans" },
  { value: "LiberationSans-Bold", label: "Liberation Sans Bold" },
];

export const SUBTITLE_POSITIONS = [
  { value: "", label: "Padrão" },
  { value: "bottom", label: "Baixo" },
  { value: "middle", label: "Meio" },
  { value: "top", label: "Topo" },
];

export const SUBTITLE_CASES = [
  { value: "", label: "Padrão" },
  { value: "upper", label: "MAIÚSCULAS" },
  { value: "lower", label: "minúsculas" },
  { value: "none", label: "Como escrito" },
];

export const SUBTITLE_COLORS = [
  { value: "", label: "Padrão" },
  { value: "white", label: "Branco" },
  { value: "yellow", label: "Amarelo" },
  { value: "cyan", label: "Ciano" },
  { value: "red", label: "Vermelho" },
  { value: "lime", label: "Verde limão" },
];

export const TRANSITION_TYPES = [
  { value: "", label: "Padrão (smoothleft)" },
  { value: "fade", label: "Fade" },
  { value: "fadeblack", label: "Fade Preto" },
  { value: "fadewhite", label: "Fade Branco" },
  { value: "wipeleft", label: "Wipe Esquerda" },
  { value: "wiperight", label: "Wipe Direita" },
  { value: "slideleft", label: "Slide Esquerda" },
  { value: "slideright", label: "Slide Direita" },
  { value: "slideup", label: "Slide Cima" },
  { value: "slidedown", label: "Slide Baixo" },
  { value: "smoothleft", label: "Smooth Esquerda" },
  { value: "smoothright", label: "Smooth Direita" },
  { value: "smoothup", label: "Smooth Cima" },
  { value: "smoothdown", label: "Smooth Baixo" },
  { value: "circleopen", label: "Círculo Abre" },
  { value: "circleclose", label: "Círculo Fecha" },
  { value: "dissolve", label: "Dissolve" },
  { value: "zoomin", label: "Zoom In" },
  { value: "hblur", label: "Blur Horizontal" },
  { value: "diagtl", label: "Diagonal TL" },
  { value: "diagtr", label: "Diagonal TR" },
  { value: "diagbl", label: "Diagonal BL" },
  { value: "diagbr", label: "Diagonal BR" },
];

export const BOX_COLORS = [
  { value: "", label: "Padrão" },
  { value: "black@0.7", label: "Preto 70%" },
  { value: "black@0.5", label: "Preto 50%" },
  { value: "black@0.3", label: "Preto 30%" },
  { value: "white@0.7", label: "Branco 70%" },
  { value: "white@0.5", label: "Branco 50%" },
];

export function VideoCustomizationControls({
  onChange,
}: {
  onChange: (opts: VideoCustomization) => void;
}) {
  const [opts, setOpts] = useState<VideoCustomization>({});
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [uploadingVoice, setUploadingVoice] = useState(false);
  const { data: voices, setData: setVoices } = usePoll(() => api.listVoices(), 10000);

  const update = (key: keyof VideoCustomization, value: string | number | boolean) => {
    const newOpts = { ...opts, [key]: value };
    // Clean up empty/zero values, but preserve booleans (false is valid)
    Object.keys(newOpts).forEach((k) => {
      const v = (newOpts as any)[k];
      if (typeof v === "boolean") return; // keep true AND false
      if (v === "" || v === 0 || v === undefined) delete (newOpts as any)[k];
    });
    setOpts(newOpts);
    onChange(newOpts);
  };

  const uploadVoice = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingVoice(true);
    try {
      const r = await api.uploadVoice(file);
      toast.success(`Voz "${r.filename}" enviada (${r.file_size_kb} KB)`);
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
      toast.success(`Voz "${filename}" excluída`);
      const updated = await api.listVoices();
      setVoices(updated);
      if (opts.voice === filename) update("voice", "");
    } catch (err: any) {
      toast.error(err.message);
    }
  };

  return (
    <div className="space-y-3 rounded-md border border-zinc-800 bg-zinc-900/50 p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-zinc-400">Customização do vídeo</span>
        <button
          className="text-xs text-violet-400 hover:underline"
          onClick={() => setShowAdvanced(!showAdvanced)}
        >
          {showAdvanced ? "Ocultar avançado" : "Mostrar avançado"}
        </button>
      </div>

      {/* Scene duration */}
      <div>
        <Label>Duração de cada cena (segundos)</Label>
        <input
          type="number"
          min={0}
          step={1}
          placeholder="0 = automático (uma cena por clip)"
          value={opts.scene_duration || ""}
          onChange={(e) => update("scene_duration", Number(e.target.value))}
          className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
        />
        <p className="mt-1 text-xs text-zinc-500">
          Ex: 10 = cenas de 10s cada · 7200 = uma cena longa de até 2h (pega um trecho aleatório contínuo).
          Se o gameplay for mais curto que a cena, encadeia outro vídeo automaticamente.
        </p>
      </div>

      {/* Video format */}
      <div>
        <Label>Formato da tela</Label>
        <select
          value={opts.video_format || ""}
          onChange={(e) => update("video_format", e.target.value)}
          className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
        >
          <option value="">Padrão (9:16)</option>
          {VIDEO_FORMATS.map((f) => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
      </div>

      {/* Voice selection */}
      <div>
        <div className="flex items-center justify-between">
          <Label>Voz da narração (TTS)</Label>
          <label className="cursor-pointer">
            <span className="inline-flex h-6 items-center rounded bg-violet-600 px-2 text-[11px] font-medium text-white hover:bg-violet-500">
              {uploadingVoice ? "Enviando..." : "Upload voz"}
            </span>
            <input
              type="file"
              className="hidden"
              onChange={uploadVoice}
              accept=".wav,.mp3,.ogg,.flac,.m4a"
            />
          </label>
        </div>
        <select
          value={opts.voice || ""}
          onChange={(e) => update("voice", e.target.value)}
          className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
        >
          <option value="">Padrão do sistema (bruno.wav)</option>
          {voices?.map((v: any) => (
            <option key={v.filename} value={v.filename}>
              {v.filename} ({v.file_size_kb} KB)
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-zinc-500">
          Envie um arquivo de áudio curto (5-30s) da voz que o XTTS deve clonar.
          {voices && voices.length > 0 && ` ${voices.length} voz(es) disponível(is).`}
        </p>
        {opts.voice && (
          <button
            className="mt-1 text-xs text-red-400 hover:underline"
            onClick={() => deleteVoice(opts.voice!)}
          >
            Excluir "{opts.voice}"
          </button>
        )}
      </div>

      {showAdvanced && (
        <div className="space-y-3 border-t border-zinc-800 pt-3">
          {/* Subtitle preview */}
          <SubtitlePreview opts={opts} />

          {/* Transitions */}
          <span className="text-xs font-semibold text-zinc-500">Transição entre cenas</span>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label>Tipo de transição</Label>
              <select
                value={opts.transition_type || ""}
                onChange={(e) => update("transition_type", e.target.value)}
                className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
              >
                {TRANSITION_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div>
              <Label>Duração (segundos)</Label>
              <input
                type="number"
                min={0}
                max={5}
                step={0.1}
                placeholder="0 = padrão (0.5s)"
                value={opts.transition_duration || ""}
                onChange={(e) => update("transition_duration", Number(e.target.value))}
                className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
              />
            </div>
          </div>

          {/* Subtitle basic */}
          <span className="text-xs font-semibold text-zinc-500">Legenda</span>

          {/* Font */}
          <div>
            <Label>Fonte da legenda</Label>
            <select
              value={opts.subtitle_font || ""}
              onChange={(e) => update("subtitle_font", e.target.value)}
              className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
            >
              {SUBTITLE_FONTS.map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </select>
          </div>

          {/* Font size */}
          <div>
            <Label>Tamanho da fonte</Label>
            <input
              type="number"
              min={0}
              placeholder="0 = automático"
              value={opts.subtitle_font_size || ""}
              onChange={(e) => update("subtitle_font_size", Number(e.target.value))}
              className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
            />
          </div>

          {/* Color */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label>Cor do texto</Label>
              <select
                value={opts.subtitle_color || ""}
                onChange={(e) => update("subtitle_color", e.target.value)}
                className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
              >
                {SUBTITLE_COLORS.map((c) => (
                  <option key={c.value} value={c.value}>{c.label}</option>
                ))}
              </select>
            </div>
            <div>
              <Label>Cor do contorno</Label>
              <select
                value={opts.subtitle_outline_color || ""}
                onChange={(e) => update("subtitle_outline_color", e.target.value)}
                className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
              >
                <option value="">Padrão (preto)</option>
                <option value="black">Preto</option>
                <option value="white">Branco</option>
                <option value="red">Vermelho</option>
              </select>
            </div>
          </div>

          {/* Position */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label>Posição</Label>
              <select
                value={opts.subtitle_position || ""}
                onChange={(e) => update("subtitle_position", e.target.value)}
                className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
              >
                {SUBTITLE_POSITIONS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
            <div>
              <Label>Caixa (case)</Label>
              <select
                value={opts.subtitle_case || ""}
                onChange={(e) => update("subtitle_case", e.target.value)}
                className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
              >
                {SUBTITLE_CASES.map((c) => (
                  <option key={c.value} value={c.value}>{c.label}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Subtitle advanced — background box */}
          <div className="space-y-2 border-t border-zinc-800/50 pt-2">
            <span className="text-xs font-semibold text-zinc-500">Fundo da legenda</span>

            <label className="flex items-center gap-2 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={opts.subtitle_box_enabled ?? false}
                onChange={(e) => update("subtitle_box_enabled", e.target.checked)}
                className="h-4 w-4 rounded border-zinc-600"
              />
              Ativar fundo (box)
            </label>

            {opts.subtitle_box_enabled && (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label>Cor do fundo</Label>
                    <select
                      value={opts.subtitle_box_color || ""}
                      onChange={(e) => update("subtitle_box_color", e.target.value)}
                      className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
                    >
                      {BOX_COLORS.map((c) => (
                        <option key={c.value} value={c.value}>{c.label}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <Label>Padding do fundo</Label>
                    <input
                      type="number"
                      min={0}
                      placeholder="0 = padrão"
                      value={opts.subtitle_box_padding || ""}
                      onChange={(e) => update("subtitle_box_padding", Number(e.target.value))}
                      className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
                    />
                  </div>
                </div>

                <label className="flex items-center gap-2 text-sm text-zinc-300">
                  <input
                    type="checkbox"
                    checked={opts.subtitle_rounded_box ?? false}
                    onChange={(e) => update("subtitle_rounded_box", e.target.checked)}
                    className="h-4 w-4 rounded border-zinc-600"
                  />
                  Fundo arredondado (pill)
                </label>
              </>
            )}
          </div>

          {/* Subtitle advanced — stroke */}
          <div className="space-y-2 border-t border-zinc-800/50 pt-2">
            <span className="text-xs font-semibold text-zinc-500">Traço (stroke)</span>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label>Cor do traço</Label>
                <select
                  value={opts.subtitle_stroke_color || ""}
                  onChange={(e) => update("subtitle_stroke_color", e.target.value)}
                  className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
                >
                  <option value="">Padrão</option>
                  <option value="black">Preto</option>
                  <option value="white">Branco</option>
                  <option value="red">Vermelho</option>
                  <option value="blue">Azul</option>
                </select>
              </div>
              <div>
                <Label>Largura do traço</Label>
                <input
                  type="number"
                  min={0}
                  placeholder="0 = padrão"
                  value={opts.subtitle_stroke_width || ""}
                  onChange={(e) => update("subtitle_stroke_width", Number(e.target.value))}
                  className="h-8 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-sm"
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
