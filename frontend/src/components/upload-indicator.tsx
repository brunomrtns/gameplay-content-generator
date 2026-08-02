import { useUploadStore, activeUploadCount } from "@/lib/upload-store";
import { Loader2, CheckCircle2, AlertCircle, Upload, X } from "lucide-react";

/**
 * Persistent upload indicator — shown in the layout header when there are
 * active uploads. This component is always mounted (in the Layout), so
 * uploads continue to show progress even when the user navigates to other
 * pages.
 *
 * Design: a small pill-shaped badge in the header that expands into a
 * dropdown panel showing individual upload progress bars.
 */
export function UploadIndicator() {
  const { uploads, removeUpload } = useUploadStore();
  const activeCount = activeUploadCount(uploads);

  if (uploads.length === 0) return null;

  return (
    <div className="group relative">
      {/* Badge */}
      <button className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-1.5 text-sm transition-all hover:border-border-bright">
        {activeCount > 0 ? (
          <Loader2 className="h-4 w-4 text-accent animate-spin" />
        ) : (
          <Upload className="h-4 w-4 text-text-muted" />
        )}
        <span className="text-text-secondary">
          {activeCount > 0 ? `${activeCount} upload(s)` : "Uploads"}
        </span>
      </button>

      {/* Dropdown panel — always visible when there are active uploads,
          otherwise only on hover */}
      <div
        className={`absolute right-0 top-full z-50 mt-2 w-80 rounded-xl border border-border bg-surface-elevated shadow-2xl transition-all duration-200 ${
          activeCount > 0
            ? "visible opacity-100"
            : "invisible opacity-0 group-hover:visible group-hover:opacity-100"
        }`}
      >
        <div className="max-h-96 overflow-y-auto p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-text-secondary">
              Uploads
            </span>
            {activeCount === 0 && (
              <button
                onClick={() => useUploadStore.getState().clearCompleted()}
                className="text-xs text-text-muted hover:text-text"
              >
                Limpar
              </button>
            )}
          </div>
          <div className="space-y-2">
            {uploads.map((u) => (
              <div
                key={u.id}
                className="flex items-center gap-2.5 rounded-lg border border-border bg-surface px-2.5 py-2"
              >
                {/* Status icon */}
                <div className="flex-shrink-0">
                  {u.status === "preparing" && (
                    <Loader2 className="h-3.5 w-3.5 text-accent animate-spin" />
                  )}
                  {u.status === "uploading" && (
                    <Loader2 className="h-3.5 w-3.5 text-accent animate-spin" />
                  )}
                  {u.status === "processing" && (
                    <Loader2 className="h-3.5 w-3.5 text-accent-warm animate-spin" />
                  )}
                  {u.status === "done" && (
                    <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                  )}
                  {u.status === "error" && (
                    <AlertCircle className="h-3.5 w-3.5 text-red-500" />
                  )}
                </div>

                {/* File info + progress */}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-1">
                    <span className="truncate text-xs font-medium">
                      {u.fileName}
                    </span>
                    <span className="flex-shrink-0 text-[10px] text-text-muted">
                      {u.status === "preparing" && "Preparando…"}
                      {u.status === "uploading" && `${u.progress}%`}
                      {u.status === "processing" && "Processando…"}
                      {u.status === "done" && "OK"}
                      {u.status === "error" && "Erro"}
                    </span>
                  </div>
                  {u.status === "preparing" && (
                    <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-surface-elevated">
                      <div className="h-full w-1/4 rounded-full bg-accent/60 animate-pulse" />
                    </div>
                  )}
                  {u.status === "uploading" && (
                    <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-surface-elevated">
                      <div
                        className="h-full rounded-full bg-accent transition-all duration-300"
                        style={{ width: `${u.progress}%` }}
                      />
                    </div>
                  )}
                  {u.status === "processing" && (
                    <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-surface-elevated">
                      <div className="h-full w-full rounded-full bg-accent-warm/60 animate-pulse" />
                    </div>
                  )}
                  {u.status === "error" && u.error && (
                    <p className="mt-0.5 truncate text-[10px] text-red-400">{u.error}</p>
                  )}
                </div>

                {/* Dismiss */}
                {(u.status === "done" || u.status === "error") && (
                  <button
                    onClick={() => removeUpload(u.id)}
                    className="flex-shrink-0 text-text-muted hover:text-text"
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
