<script lang="ts">
  import { cn } from "@/ui/cn";
  import Chart from "./Chart.svelte";
  import { toneBackground } from "./styles";
  import type { ChartTone } from "./types";

  /**
   * Ranked horizontal bars, such as the blocks in a schematic. The largest `limit` items are
   * shown in order and the rest fold into one `otherLabel` bar. Labels and values are text, so
   * the bars need no separate data table, and the chart works without JavaScript.
   */
  type Props = {
    title: string;
    description: string;
    items: { label: string; value: number; href?: string }[];
    limit?: number;
    otherLabel: string;
    valueFormat?: (value: number) => string;
    tone?: ChartTone;
    class?: string;
  };
  let {
    title,
    description,
    items,
    limit = 12,
    otherLabel,
    valueFormat = String,
    tone = 1,
    class: className,
  }: Props = $props();

  const ranked = $derived(items.toSorted((a, b) => b.value - a.value));
  const shown = $derived.by(() => {
    const rest = ranked.slice(limit);
    const other = rest.reduce((sum, item) => sum + item.value, 0);
    return [
      ...ranked.slice(0, limit).map((item) => ({ ...item, other: false })),
      ...(rest.length > 0 ? [{ label: otherLabel, value: other, other: true }] : []),
    ];
  });
  const max = $derived(Math.max(1, ...shown.map((item) => item.value)));
</script>

<Chart {title} {description} group class={className}>
  {#snippet plot()}
    <ol class="grid gap-2 text-sm">
      {#each shown as item (item.label)}
        <li
          class="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 max-sm:grid-cols-[1fr_auto]"
        >
          <span class={cn("truncate", item.other && "text-fg-muted")} title={item.label}>
            {#if item.href !== undefined}<a href={item.href} class="text-fg">{item.label}</a
              >{:else}{item.label}{/if}
          </span>
          <span class="h-3 rounded-e-xs bg-surface-muted max-sm:col-span-2 max-sm:row-start-2">
            <!-- eslint-disable svelte/no-inline-styles -- bar length is the data -->
            <span
              class={cn(
                "block h-full rounded-e-xs",
                item.other ? toneBackground.muted : toneBackground[tone],
              )}
              style:width={`${(item.value / max) * 100}%`}
            ></span>
            <!-- eslint-enable svelte/no-inline-styles -->
          </span>
          <span class="text-end font-semibold tabular-nums max-sm:col-start-2 max-sm:row-start-1"
            >{valueFormat(item.value)}</span
          >
        </li>
      {/each}
    </ol>
  {/snippet}
</Chart>
