import { VideoCustomization } from "@/components/video-customization";

const FORMAT_DIMENSIONS: Record<string, { w: number; h: number }> = {
  "9:16": { w: 1080, h: 1920 },
  "16:9": { w: 1920, h: 1080 },
  "1:1": { w: 1080, h: 1080 },
  "4:5": { w: 1080, h: 1350 },
};

const SIZE_MAP: Record<string, string> = {
  medium: "1.8rem",
  large: "2.5rem",
  xlarge: "3.5rem",
};

const POSITION_MAP: Record<string, string> = {
  top: "15%",
  middle: "40%",
  bottom: "70%",
};

export interface PresentationPreviewOpts {
  video_format?: string;
  thumbnail_text_enabled?: boolean;
  thumbnail_text_source?: string;
  thumbnail_text_custom?: string;
  thumbnail_text_position?: string;
  thumbnail_text_color?: string;
  thumbnail_text_outline?: string;
  thumbnail_text_size?: string;
  thumbnail_image_path?: string;
}

export function ThumbnailPreview({
  opts,
  title = "Título do vídeo",
}: {
  opts: PresentationPreviewOpts;
  title?: string;
}) {
  const fmt = opts.video_format || "9:16";
  const dims = FORMAT_DIMENSIONS[fmt] || FORMAT_DIMENSIONS["9:16"];
  const aspectRatio = dims.w / dims.h;

  const fontSize = SIZE_MAP[opts.thumbnail_text_size || "large"] || "2.5rem";
  const topPos = POSITION_MAP[opts.thumbnail_text_position || "bottom"] || "70%";
  const textColor = opts.thumbnail_text_color || "white";
  const outlineColor = opts.thumbnail_text_outline || "black";

  const displayText =
    opts.thumbnail_text_source === "custom"
      ? opts.thumbnail_text_custom || "Texto personalizado"
      : title;

  return (
    <div
      className="relative mx-auto overflow-hidden rounded-lg border border-border bg-black"
      style={{ aspectRatio: `${aspectRatio}`, maxHeight: "320px", width: "auto", height: "100%" }}
    >
      {/* Background image or placeholder */}
      {opts.thumbnail_image_path ? (
        <img
          src={opts.thumbnail_image_path}
          alt="Thumbnail preview"
          className="absolute inset-0 h-full w-full object-cover"
        />
      ) : (
        <div className="absolute inset-0 bg-gradient-to-br from-gray-800 to-gray-900" />
      )}

      {/* Dark overlay for text readability */}
      {opts.thumbnail_text_enabled && (
        <div
          className="absolute inset-x-0"
          style={{
            top: topPos,
            height: "30%",
            background: `linear-gradient(to bottom, ${outlineColor}00, ${outlineColor}80, ${outlineColor}00)`,
          }}
        />
      )}

      {/* Title text */}
      {opts.thumbnail_text_enabled && displayText && (
        <div
          className="absolute inset-x-0 px-4 text-center font-bold z-20"
          style={{
            top: topPos,
            transform: "translateY(-50%)",
            fontSize,
            color: textColor,
            textShadow: `2px 2px 0 ${outlineColor}, -2px -2px 0 ${outlineColor}, 2px -2px 0 ${outlineColor}, -2px 2px 0 ${outlineColor}`,
            lineHeight: 1.2,
            wordBreak: "break-word",
          }}
        >
          {displayText}
        </div>
      )}

      {/* Placeholder when no image (auto mode) — shows a gameplay-style mockup */}
      {!opts.thumbnail_image_path && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
          {/* Mock gameplay frame: dark gradient with a subtle "scene" feel */}
          <div className="absolute inset-0 bg-gradient-to-br from-slate-700 via-slate-800 to-slate-900" />
          <div className="absolute inset-0 opacity-20 bg-[radial-gradient(ellipse_at_center,rgba(255,255,255,0.15),transparent_70%)]" />
          {/* Icon + label */}
          <div className="relative flex flex-col items-center gap-1.5 z-10">
            <svg
              className="h-8 w-8 text-teal-400/60"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="m15.75 10.5 4.72-4.72a.75.75 0 0 1 1.28.53v11.38a.75.75 0 0 1-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 0 0 2.25-2.25v-9a2.25 2.25 0 0 0-2.25-2.25h-9A2.25 2.25 0 0 0 2.25 7.5v9a2.25 2.25 0 0 0 2.25 2.25Z" />
            </svg>
            <span className="text-[10px] font-medium text-teal-400/70 uppercase tracking-wider">
              Frame automático
            </span>
            <span className="text-[9px] text-gray-400 text-center max-w-[80%]">
              O sistema vai selecionar o melhor frame do gameplay
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
