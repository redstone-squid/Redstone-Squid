<script lang="ts">
  import { cn } from "@/ui/cn";

  /**
   * Two or three facts of the same kind about one item, such as a holder count and a version,
   * separated by dots. The dots are hidden from assistive technology, which reads a list.
   * Facts with different roles belong in different places: the eyebrow, a `Badge`, or a
   * `DescriptionList`.
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
    "flex min-w-0 list-none flex-wrap gap-x-2 text-fg-muted",
    size === "xs" ? "text-xs" : "text-sm",
    className,
  )}
>
  {#each items as item, index (index)}
    <li class="flex gap-x-2">
      {#if index > 0}<span aria-hidden="true">·</span>{/if}{item}
    </li>
  {/each}
</ul>
