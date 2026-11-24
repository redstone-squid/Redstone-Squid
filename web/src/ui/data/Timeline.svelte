<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  type Entry = {
    id: string | number;
    datetime: string;
    dateLabel: string;
    title: string;
    href?: string;
    description?: string;
    tone?: "default" | "accent" | "success" | "warning" | "danger";
  };

  /**
   * Events in order, such as a build's edits or a record's holders. `details` renders extra
   * content under an entry, such as a `Diff`.
   */
  type Props = { items: Entry[]; label?: string; details?: Snippet<[Entry]>; class?: string };
  let { items, label, details, class: className }: Props = $props();
  const dots = {
    default: "border-line-strong bg-surface",
    accent: "border-accent bg-accent",
    success: "border-success bg-success",
    warning: "border-warning bg-warning",
    danger: "border-danger bg-danger",
  };
</script>

<ol aria-label={label} class={cn("grid min-w-0", className)}>
  {#each items as entry, index (entry.id)}
    <li class="relative grid grid-cols-[1rem_minmax(0,1fr)] gap-x-4 pb-6 last:pb-0">
      {#if index < items.length - 1}<span
          class="absolute start-1.75 top-4 bottom-0 w-0.5 bg-line"
          aria-hidden="true"
        ></span>{/if}
      <span
        class={cn(
          "relative mt-1 size-4 rounded-full border-2",
          entry.tone === undefined ? dots.default : dots[entry.tone],
        )}
        aria-hidden="true"
      ></span>
      <div class="grid min-w-0 gap-1">
        <time class="text-xs text-fg-muted" datetime={entry.datetime}>{entry.dateLabel}</time>
        <p class="font-semibold wrap-break-word">
          {#if entry.href !== undefined}<a href={entry.href}>{entry.title}</a
            >{:else}{entry.title}{/if}
        </p>
        {#if entry.description}<p class="text-sm text-fg-muted">{entry.description}</p>{/if}
        {@render details?.(entry)}
      </div>
    </li>
  {/each}
</ol>
