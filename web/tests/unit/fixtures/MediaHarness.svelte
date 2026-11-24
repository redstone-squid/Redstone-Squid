<script lang="ts">
  import type { MediaItem, MediaStatus } from "@/ui/patterns/media";
  import MediaUploader from "@/ui/patterns/MediaUploader.svelte";

  let items = $state<MediaItem[]>([
    { id: "a", name: "front.png", status: "ready" },
    { id: "b", name: "side.png", status: "uploading", progress: 30 },
  ]);
  const set = (id: string, status: MediaStatus) =>
    (items = items.map((item) => (item.id === id ? { ...item, status } : item)));
  const status: Record<MediaStatus, string> = {
    queued: "Queued",
    uploading: "Uploading",
    processing: "Processing",
    ready: "Ready",
    failed: "Failed",
    removed: "Removed",
  };
</script>

<button type="button" onclick={() => set("b", "failed")}>Fail side</button>
<button type="button" onclick={() => set("b", "ready")}>Finish side</button>
<MediaUploader
  {items}
  label="Media"
  labels={{
    drop: "Drop",
    browse: "Browse",
    status,
    primary: "Cover",
    remove: (name) => `Remove ${name}`,
    undo: "Undo",
    retry: "Retry",
    setPrimary: (name) => `Use ${name} as cover`,
    summary: ({ ready, failed }) => `${ready} ready, ${failed} failed`,
  }}
  onFiles={() => undefined}
  onRetry={(id) => set(id, "uploading")}
  onRemove={(id) => set(id, "removed")}
  onUndoRemove={(id) => set(id, "ready")}
/>
