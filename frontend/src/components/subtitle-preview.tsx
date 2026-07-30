import { VideoCustomization } from "@/components/video-customization";

const FORMAT_DIMENSIONS: Record<string, { w: number; h: number }> = {
  "9:16": { w: 1080, h: 1920 },
  "16:9": { w: 1920, h: 1080 },
  "1:1": { w: 1080, h: 1080 },
  "4:5": { w: 1080, h: 1350 },
};

const COLOR_MAP: Record<string, string> = {
  white: "#ffffff",
  yellow: "#ffff00",
  cyan: "#00ffff",
  red: "#ff0000",
  lime: "#32cd32",
  black: "#000000",
  blue: "#0000ff",
};

/**
 * Parse FFmpeg-style color with alpha (e.g. "black@0.7") into CSS rgba.
 */
function parseColor(input: string, fallback: string): string {
  if (!input) return fallback;
  const baseColor = COLOR_MAP[input.split("@")[0]] || fallback;
  const alphaMatch = input.match(/@([\d.]+)/);
  if (alphaMatch) {
    const alpha = parseFloat(alphaMatch[1]);
    const hex = baseColor;
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return baseColor;
}

/**
 * Maps subtitle position string to CSS vertical placement.
 * Returns the `bottom` offset (distance from bottom edge).
 * "top" → near top (large bottom value), "middle" → center, "bottom" → near bottom (small bottom value)
 */
function positionToBottomOffset(pos: string): string {
  switch (pos) {
    case "top":
      return "82%"; // near top = far from bottom
    case "middle":
      return "50%";
    default:
      return "8%"; // bottom / default = close to bottom
  }
}

/**
 * Maps FFmpeg font names to CSS font-family strings.
 * These are the fonts available in the Docker container (DejaVu, Liberation).
 */
const FONT_MAP: Record<string, string> = {
  "DejaVuSans-Bold": "'DejaVu Sans', sans-serif",
  "DejaVuSans": "'DejaVu Sans', sans-serif",
  "LiberationSans-Bold": "'Liberation Sans', 'Arial', sans-serif",
  "LiberationSans": "'Liberation Sans', 'Arial', sans-serif",
  Arial: "Arial, sans-serif",
  Helvetica: "Helvetica, Arial, sans-serif",
  "Courier-New": "'Courier New', monospace",
  "Courier New": "'Courier New', monospace",
};

function fontToCss(font: string | undefined): string {
  if (!font) return "Arial, sans-serif";
  return FONT_MAP[font] || "Arial, sans-serif";
}

export function SubtitlePreview({ opts }: { opts: VideoCustomization }) {
  const fmt = opts.video_format || "9:16";
  const dims = FORMAT_DIMENSIONS[fmt] || FORMAT_DIMENSIONS["9:16"];
  const aspectRatio = dims.w / dims.h;

  // Subtitle style computation
  const fontSize = opts.subtitle_font_size || 48;
  const fontColor = COLOR_MAP[opts.subtitle_color || ""] || "#ffffff";
  const outlineColor = COLOR_MAP[opts.subtitle_outline_color || ""] || "#000000";
  const strokeColor = COLOR_MAP[opts.subtitle_stroke_color || ""] || "";
  const strokeW = opts.subtitle_stroke_width || 0;
  const effectiveOutlineColor = strokeColor || outlineColor;
  const effectiveOutlineW = strokeW || 3;

  const textShadow = Array.from({ length: 8 }, (_, i) => {
    const angle = (i * Math.PI) / 4;
    const x = Math.cos(angle) * effectiveOutlineW;
    const y = Math.sin(angle) * effectiveOutlineW;
    return `${x}px ${y}px 0 ${effectiveOutlineColor}`;
  }).join(", ");

  const boxEnabled = opts.subtitle_box_enabled ?? false;
  const boxColor = parseColor(opts.subtitle_box_color || "black@0.7", "rgba(0,0,0,0.7)");
  const boxPadding = opts.subtitle_box_padding || 10;
  const rounded = opts.subtitle_rounded_box ?? false;

  const bottomOffset = positionToBottomOffset(opts.subtitle_position || "");
  const textTransform =
    opts.subtitle_case === "upper"
      ? "uppercase"
      : opts.subtitle_case === "lower"
        ? "lowercase"
        : "none";

  const sampleText = "EXEMPLO DE LEGENDA NO VÍDEO";

  return (
    <div className="space-y-2">
      <span className="text-xs font-semibold text-zinc-500">Preview da legenda</span>
      <div
        className="relative mx-auto overflow-hidden rounded-lg bg-gradient-to-br from-zinc-700 to-zinc-900"
        style={{
          aspectRatio: `${aspectRatio}`,
          maxHeight: "240px",
          width: aspectRatio < 1 ? "auto" : "100%",
          maxWidth: aspectRatio < 1 ? `${240 * aspectRatio}px` : "100%",
        }}
      >
        {/* Simulated gameplay background */}
        <div className="absolute inset-0 opacity-40 bg-[radial-gradient(circle_at_50%_40%,#555,#222)]" />

        {/* Subtitle */}
        <div
          className="absolute left-1/2 text-center font-bold leading-tight"
          style={{
            top:
              opts.subtitle_position === "top"
                ? "8%"
                : opts.subtitle_position === "middle"
                  ? "50%"
                  : "auto",
            bottom: opts.subtitle_position === "top" || opts.subtitle_position === "middle" ? "auto" : bottomOffset,
            transform:
              opts.subtitle_position === "middle"
                ? "translate(-50%, -50%)"
                : "translateX(-50%)",
            fontSize: `${Math.max(10, Math.min(22, fontSize / 3))}px`,
            color: fontColor,
            textShadow: textShadow,
            textTransform: textTransform,
            backgroundColor: boxEnabled ? boxColor : "transparent",
            padding: boxEnabled ? `${boxPadding / 3}px ${boxPadding / 2}px` : "0",
            borderRadius: rounded ? "9999px" : "0",
            maxWidth: "90%",
            fontFamily: fontToCss(opts.subtitle_font),
            fontWeight: opts.subtitle_font?.includes("Bold") ? "bold" : "normal",
          }}
        >
          {sampleText}
        </div>
      </div>
      <p className="text-xs text-zinc-500">
        Preview aproximado. O resultado final pode variar conforme a fonte e resolução.
      </p>
    </div>
  );
}
