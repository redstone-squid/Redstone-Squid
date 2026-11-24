<script lang="ts">
  import { cn } from "@/ui/cn";

  type Item = { term: string; value: string; numeric?: boolean; href?: string };

  /**
   * Labelled values, such as a build's specifications. `stacked` puts each term above its
   * value, `inline` puts pairs side by side in a wrapping row, and `grid` lays pairs out in
   * responsive columns. `numeric` uses tabular figures for measurements.
   */
  type Props = {
    items: Item[];
    layout?: "stacked" | "inline" | "grid";
    size?: "sm" | "md";
    divided?: boolean;
    class?: string;
  };
  let {
    items,
    layout = "stacked",
    size = "md",
    divided = false,
    class: className,
  }: Props = $props();
  const layouts = {
    stacked: "grid gap-3",
    inline: "flex flex-wrap gap-x-6 gap-y-3",
    grid: "grid grid-cols-[repeat(auto-fill,minmax(min(100%,var(--container-tile)),1fr))] gap-4",
  };
</script>

<dl
  class={cn(
    "min-w-0",
    size === "sm" ? "text-xs" : "text-sm",
    layouts[layout],
    divided && "border-t border-line pt-3",
    className,
  )}
>
  {#each items as item (item.term)}
    <div class="min-w-0">
      <dt class="text-fg-muted">{item.term}</dt>
      <dd class={cn("mt-0.5 wrap-break-word text-fg", item.numeric === true && "tabular-nums")}>
        {#if item.href !== undefined}<a href={item.href}>{item.value}</a>{:else}{item.value}{/if}
      </dd>
    </div>
  {/each}
</dl>
