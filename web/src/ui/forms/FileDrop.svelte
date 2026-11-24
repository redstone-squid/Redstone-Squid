<script lang="ts">
  import Upload from "@lucide/svelte/icons/upload";
  import { cn } from "@/ui/cn";
  import type { fieldAttributes, FieldMessages } from "@/ui/forms/field";
  import FieldFrame from "./FieldFrame.svelte";
  import { accepts, takeSelectedFiles } from "./upload";

  /**
   * Choose files with the native picker, or drop them on the zone. The file input is the
   * focusable control, so the keyboard and screen readers use the browser's picker; dropping
   * is an extra. Choosing the same file again fires `onFiles` again.
   *
   * `onFiles` receives the files that match `accept` and `maxBytes`, then the rest, so the
   * feature can explain why a file was skipped. Without `multiple`, a drop keeps one file.
   */
  type Props = Omit<FieldMessages, "width" | "labelVisibility"> & {
    name?: string;
    accept?: string;
    multiple?: boolean;
    maxBytes?: number;
    disabled?: boolean;
    labels: { drop: string; browse: string };
    onFiles: (files: File[], rejected: File[]) => void;
    class?: string;
  };
  let {
    name,
    accept,
    multiple = false,
    maxBytes,
    disabled = false,
    labels,
    onFiles,
    ...frame
  }: Props = $props();
  let dragging = $state(false);

  function deliver(files: File[]): void {
    const chosen = multiple ? files : files.slice(0, 1);
    const fits = (file: File) =>
      accepts(file, accept) && (maxBytes === undefined || file.size <= maxBytes);
    if (chosen.length > 0)
      onFiles(
        chosen.filter(fits),
        chosen.filter((file) => !fits(file)),
      );
  }
</script>

{#snippet zone(attributes: ReturnType<typeof fieldAttributes>)}
  <div
    role="presentation"
    class={cn(
      "relative grid min-w-0 justify-items-center gap-2 rounded-md border-2 border-dashed px-4 py-6 text-center",
      "transition-[border-color,background-color] duration-150 ease-standard",
      "has-focus-visible:outline-3 has-focus-visible:outline-offset-3 has-focus-visible:outline-focus",
      disabled
        ? "cursor-not-allowed border-line bg-surface-muted text-fg-muted"
        : dragging
          ? "border-accent bg-accent-soft"
          : attributes["aria-invalid"] === true
            ? "border-danger bg-surface"
            : "border-line-strong bg-surface hover:border-fg-muted",
    )}
    ondragenter={(event) => {
      if (disabled) return;
      event.preventDefault();
      dragging = true;
    }}
    ondragover={(event) => {
      if (!disabled) event.preventDefault();
    }}
    ondragleave={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) dragging = false;
    }}
    ondrop={(event) => {
      if (disabled) return;
      event.preventDefault();
      dragging = false;
      deliver(Array.from(event.dataTransfer?.files ?? []));
    }}
  >
    <Upload class="size-6 text-fg-muted" aria-hidden="true" />
    <span class="text-sm text-fg-muted">{labels.drop}</span>
    <span
      class={cn(
        "inline-flex min-h-control-sm items-center rounded-sm border px-3 text-sm font-semibold",
        disabled ? "border-line text-fg-muted" : "border-line-strong bg-surface text-fg",
      )}
      aria-hidden="true">{labels.browse}</span
    >
    <!-- The input covers the zone, so a click anywhere opens the picker. -->
    <input
      {...attributes}
      type="file"
      {name}
      {accept}
      {multiple}
      {disabled}
      class="absolute inset-0 size-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
      onchange={(event) => deliver(takeSelectedFiles(event.currentTarget))}
    />
  </div>
{/snippet}

<FieldFrame {...frame} children={zone} />
