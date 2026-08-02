import { create } from "zustand";

export interface UploadItem {
  id: string;
  fileName: string;
  fileSize: number;
  progress: number;
  status: "uploading" | "processing" | "done" | "error";
  error?: string;
  /** "gameplay" for video files, "knowledge" for knowledge documents */
  kind: "gameplay" | "knowledge";
}

interface UploadState {
  uploads: UploadItem[];
  addUpload: (item: UploadItem) => void;
  updateUpload: (id: string, patch: Partial<UploadItem>) => void;
  removeUpload: (id: string) => void;
  clearCompleted: () => void;
}

/**
 * Global upload store — persists across page navigation.
 *
 * This solves the problem where uploads would be lost when the user
 * navigated away from the Content page. Now uploads continue in the
 * background and a persistent indicator in the layout shows progress.
 */
export const useUploadStore = create<UploadState>((set) => ({
  uploads: [],
  addUpload: (item) => set((s) => ({ uploads: [...s.uploads, item] })),
  updateUpload: (id, patch) =>
    set((s) => ({
      uploads: s.uploads.map((u) => (u.id === id ? { ...u, ...patch } : u)),
    })),
  removeUpload: (id) =>
    set((s) => ({ uploads: s.uploads.filter((u) => u.id !== id) })),
  clearCompleted: () =>
    set((s) => ({ uploads: s.uploads.filter((u) => u.status === "uploading" || u.status === "processing") })),
}));

/** Helper: count active uploads (uploading or processing) */
export function activeUploadCount(uploads: UploadItem[]): number {
  return uploads.filter((u) => u.status === "uploading" || u.status === "processing").length;
}
