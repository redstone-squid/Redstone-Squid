/** Where one file in a `MediaUploader` stands. `removed` keeps the row so it can be undone. */
export type MediaStatus = "queued" | "uploading" | "processing" | "ready" | "failed" | "removed";

export type MediaItem = {
  id: string;
  name: string;
  status: MediaStatus;
  /** Upload progress from 0 to 100, while `uploading`. */
  progress?: number;
  thumb?: { src: string; width: number; height: number };
  error?: string;
  primary?: boolean;
};

/**
 * The text a `MediaUploader` shows. Icon buttons take the file name, so each is named
 * uniquely; Undo and Retry are described by the file name instead.
 */
export type MediaLabels = {
  drop: string;
  browse: string;
  status: Record<MediaStatus, string>;
  primary: string;
  remove: (name: string) => string;
  undo: string;
  retry: string;
  setPrimary: (name: string) => string;
  /** The aggregate announcement, such as "2 files uploaded, 1 failed". */
  summary: (counts: { ready: number; failed: number }) => string;
};
