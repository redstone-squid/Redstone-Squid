<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";
  import type { SaveState } from "@/ui/feedback/save-state";
  import SaveStatus from "@/ui/feedback/SaveStatus.svelte";

  /**
   * Settings that each save as soon as they change, one row per setting. Each `control` is a
   * labelled field, usually a `Switch`, and shows its own error; the row reports its save
   * `state` beside it. There is no undo: changing a setting back reverses it. `onRetry`
   * receives the row's id.
   */
  type Props = {
    title: string;
    description?: string;
    items: { id: string; control: Snippet; state: SaveState }[];
    labels: Record<SaveState, string> & { retry?: string };
    onRetry?: (id: string) => void;
    class?: string;
  };
  let { title, description, items, labels, onRetry, class: className }: Props = $props();
  const id = $props.id();
</script>

<section aria-labelledby={`${id}-title`} class={cn("grid min-w-0 gap-3", className)}>
  <div class="grid gap-1">
    <h2 id={`${id}-title`} class="text-lg font-bold">{title}</h2>
    {#if description !== undefined}<p class="text-sm text-fg-muted">{description}</p>{/if}
  </div>
  <ul class="divide-y divide-line rounded-md border border-line bg-surface">
    {#each items as item (item.id)}
      <li class="grid min-w-0 gap-2 px-4 py-3">
        {@render item.control()}
        {#if item.state !== "idle"}
          <SaveStatus
            state={item.state}
            {labels}
            onRetry={onRetry === undefined ? undefined : () => onRetry(item.id)}
          />
        {/if}
      </li>
    {/each}
  </ul>
</section>
