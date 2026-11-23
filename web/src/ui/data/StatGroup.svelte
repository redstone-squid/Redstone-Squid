<script lang="ts">
  import { cn } from "@/ui/cn";

  type Stat = { label: string; value: string; unit?: string; hint?: string; href?: string };

  /** Headline numbers, such as a build's size and speed or a creator's record count. */
  type Props = { items: Stat[]; size?: "md" | "lg"; class?: string };
  let { items, size = "md", class: className }: Props = $props();
</script>

<dl
  class={cn(
    "grid min-w-0 grid-cols-[repeat(auto-fill,minmax(min(100%,var(--container-tile)),1fr))] gap-4",
    className,
  )}
>
  {#each items as stat (stat.label)}
    <div class="grid min-w-0 content-start gap-1 rounded-sm border border-line bg-surface p-4">
      <dt class="text-sm text-fg-muted">{stat.label}</dt>
      <dd
        class={cn(
          "font-bold tracking-tight wrap-break-word",
          size === "lg" ? "text-3xl" : "text-2xl",
        )}
      >
        {#if stat.href !== undefined}<a class="no-underline hover:underline" href={stat.href}
            >{stat.value}</a
          >{:else}{stat.value}{/if}{#if stat.unit}<span
            class="ms-1 text-sm font-normal tracking-normal text-fg-muted">{stat.unit}</span
          >{/if}
      </dd>
      {#if stat.hint}<dd class="text-xs text-fg-muted">{stat.hint}</dd>{/if}
    </div>
  {/each}
</dl>
