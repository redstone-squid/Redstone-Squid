<script lang="ts">
  import FileIcon from "@lucide/svelte/icons/file";
  import Star from "@lucide/svelte/icons/star";
  import Trash from "@lucide/svelte/icons/trash-2";
  import { onDestroy, tick } from "svelte";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import { hideWhenBroken } from "@/ui/data/hide-when-broken";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Progress from "@/ui/feedback/Progress.svelte";
  import FileDrop from "@/ui/forms/FileDrop.svelte";
  import type { MediaItem, MediaLabels } from "./media";

  /**
   * Files attached to a draft: a `FileDrop` and one row per file with its status, progress,
   * error and actions. The feature uploads and passes back `items`.
   *
   * Removing needs no confirmation: the row becomes "Removed" with Undo, and focus moves to
   * Undo, then back to Remove after undoing. Screen readers hear one summary of finished and
   * failed files a second after the last change, instead of every row.
   */
  type Props = {
    items: MediaItem[];
    label: string;
    hint?: string;
    error?: string;
    /** The limits in words, such as "Up to 10 images, 8 MB each". */
    limitsLabel?: string;
    accept?: string;
    maxBytes?: number;
    multiple?: boolean;
    disabled?: boolean;
    labels: MediaLabels;
    onFiles: (files: File[], rejected: File[]) => void;
    onRetry: (id: string) => void;
    onRemove: (id: string) => void;
    onUndoRemove: (id: string) => void;
    onSetPrimary?: (id: string) => void;
    class?: string;
  };
  let {
    items,
    label,
    hint,
    error,
    limitsLabel,
    accept,
    maxBytes,
    multiple = true,
    disabled = false,
    labels,
    onFiles,
    onRetry,
    onRemove,
    onUndoRemove,
    onSetPrimary,
    class: className,
  }: Props = $props();
  const id = $props.id();
  let list = $state<HTMLElement>();

  async function act(itemId: string, run: (id: string) => void, focus: "undo" | "remove") {
    run(itemId);
    await tick();
    list
      ?.querySelector<HTMLElement>(`[data-media="${CSS.escape(itemId)}"] [data-${focus}]`)
      ?.focus();
  }

  const counts = $derived({
    ready: items.filter((item) => item.status === "ready").length,
    failed: items.filter((item) => item.status === "failed").length,
  });
  let announcement = $state("");
  let timer: ReturnType<typeof setTimeout> | undefined;
  let previous: string | undefined;
  $effect(() => {
    const key = `${counts.ready}/${counts.failed}`;
    const text = labels.summary(counts);
    if (previous === undefined || previous === key) {
      previous = key;
      return;
    }
    previous = key;
    clearTimeout(timer);
    timer = setTimeout(() => (announcement = text), 1000);
  });
  onDestroy(() => clearTimeout(timer));
</script>

<div class={cn("grid min-w-0 gap-3", className)}>
  <FileDrop
    {label}
    hint={[hint, limitsLabel].filter(Boolean).join(" ")}
    {error}
    {accept}
    {maxBytes}
    {multiple}
    {disabled}
    labels={{ drop: labels.drop, browse: labels.browse }}
    {onFiles}
  />
  {#if items.length > 0}
    <ul bind:this={list} class="divide-y divide-line rounded-md border border-line bg-surface">
      {#each items as item (item.id)}
        <li data-media={item.id} class="flex min-w-0 items-center gap-3 px-4 py-3">
          {#if item.status === "removed"}
            <p class="min-w-0 flex-1 text-sm text-fg-muted">
              <span id={`${id}-${item.id}`} class="wrap-anywhere line-through">{item.name}</span>
              · {labels.status.removed}
            </p>
            <Button
              size="sm"
              variant="quiet"
              data-undo
              aria-describedby={`${id}-${item.id}`}
              onclick={() => act(item.id, onUndoRemove, "remove")}>{labels.undo}</Button
            >
          {:else}
            <!-- The file icon sits under the thumbnail, so it shows when the image is missing or broken. -->
            <div
              class="grid size-12 shrink-0 place-items-center overflow-hidden rounded-sm bg-surface-muted text-fg-muted *:col-start-1 *:row-start-1"
            >
              <FileIcon class="size-5" aria-hidden="true" />
              {#if item.thumb}<img
                  class="size-full object-cover"
                  src={item.thumb.src}
                  width={item.thumb.width}
                  height={item.thumb.height}
                  alt=""
                  loading="lazy"
                  {@attach hideWhenBroken}
                />{/if}
            </div>
            <div class="grid min-w-0 flex-1 gap-1 text-sm">
              <p class="flex min-w-0 flex-wrap items-center gap-2">
                <span id={`${id}-${item.id}`} class="font-medium wrap-anywhere">{item.name}</span>
                {#if item.primary === true}<Badge tone="accent">{labels.primary}</Badge>{/if}
              </p>
              {#if item.status === "uploading" || item.status === "processing"}
                <Progress
                  label={`${item.name}: ${labels.status[item.status]}`}
                  labelVisibility="hidden"
                  value={item.status === "uploading" ? item.progress : undefined}
                />
              {/if}
              <p class={item.status === "failed" ? "text-danger" : "text-fg-muted"}>
                {item.status === "failed" && item.error !== undefined
                  ? item.error
                  : labels.status[item.status]}
              </p>
            </div>
            <div class="flex shrink-0 flex-wrap items-center justify-end gap-1">
              {#if item.status === "failed"}
                <Button
                  size="sm"
                  aria-describedby={`${id}-${item.id}`}
                  onclick={() => onRetry(item.id)}>{labels.retry}</Button
                >
              {/if}
              {#if onSetPrimary && item.status === "ready" && item.primary !== true}
                <Button
                  size="sm"
                  variant="quiet"
                  iconOnly
                  icon={Star}
                  label={labels.setPrimary(item.name)}
                  onclick={() => onSetPrimary(item.id)}
                />
              {/if}
              <Button
                size="sm"
                variant="quiet"
                iconOnly
                icon={Trash}
                data-remove
                label={labels.remove(item.name)}
                onclick={() => act(item.id, onRemove, "undo")}
              />
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
  <span class="sr-only" role="status">{announcement}</span>
</div>
