<script lang="ts">
  import { takeSelectedFiles, type FilePickerProps } from "@/ui/upload";
  let { label, onFiles, accept, multiple, disabled }: FilePickerProps = $props();
</script>

<label class="grid min-w-0 gap-2 text-sm font-semibold">
  <span>{label}</span>
  <input
    type="file"
    class="min-h-11 w-full min-w-0 rounded-sm border border-dashed border-line bg-surface p-2 font-normal text-fg-muted file:me-3 file:cursor-pointer file:rounded-sm file:border file:border-line file:bg-surface-muted file:px-3 file:py-1 file:font-semibold file:text-fg hover:not-disabled:file:border-fg-muted disabled:file:cursor-not-allowed disabled:file:text-fg-muted"
    {accept}
    {multiple}
    {disabled}
    onchange={(event) => {
      const files = takeSelectedFiles(event.currentTarget);
      if (files.length > 0) onFiles(files);
    }}
  />
</label>
