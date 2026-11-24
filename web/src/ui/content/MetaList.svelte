<script lang="ts">
  import { cn } from "@/ui/cn";

  /**
   * Short facts about one item, such as its version scope, holder count, and date, in a
   * wrapping muted row. Spacing separates the facts, so they carry no punctuation.
   */
  type Props = {
    items: string[];
    size?: "xs" | "sm";
    class?: string;
  };
  let { items, size = "sm", class: className }: Props = $props();
</script>

<ul
  class={cn(
    "flex min-w-0 list-none flex-wrap gap-x-4 gap-y-1 text-fg-muted",
    size === "xs" ? "text-xs" : "text-sm",
    className,
  )}
>
  {#each items as item, index (index)}<li>{item}</li>{/each}
</ul>
