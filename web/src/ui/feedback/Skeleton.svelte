<script lang="ts">
  import { cn } from "@/ui/cn";

  /**
   * A placeholder with the shape of content that is loading inside an island. It is hidden
   * from screen readers: mark the region `aria-busy` and say what is loading in its label.
   */
  type Props = {
    shape?: "text" | "heading" | "block" | "avatar" | "row";
    lines?: number;
    width?: "full" | "3/4" | "1/2" | "1/3";
    class?: string;
  };
  let { shape = "text", lines = 1, width = "full", class: className }: Props = $props();
  const widths = { full: "w-full", "3/4": "w-3/4", "1/2": "w-1/2", "1/3": "w-1/3" };
  const shapes = {
    text: "h-4 rounded-xs",
    heading: "h-7 rounded-xs",
    block: "aspect-8/5 rounded-md",
    avatar: "size-10 rounded-full",
    row: "h-control rounded-sm",
  };
</script>

<div class={cn("grid gap-2", className)} aria-hidden="true">
  {#each { length: shape === "text" ? lines : 1 }, index (index)}
    <div
      class={cn(
        "animate-pulse bg-surface-muted",
        shapes[shape],
        shape !== "avatar" && (index === lines - 1 && lines > 1 ? "w-2/3" : widths[width]),
      )}
    ></div>
  {/each}
</div>
