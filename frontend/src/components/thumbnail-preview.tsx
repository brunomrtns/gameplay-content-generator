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
          className="absolute inset-x-0 px-4 text-center font-bold"
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

      {/* Placeholder label when no image */}
      {!opts.thumbnail_image_path && (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-xs text-gray-500">Sem imagem — modo auto selecionará um frame</span>
        </div>
      )}
    </div>
  );
}
