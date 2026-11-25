<script lang="ts">
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /** Content hidden behind a summary line, such as evidence or a traceback. Works without JavaScript. */
  type Props = {
    summary: string;
    open?: boolean;
    variant?: "plain" | "card";
    class?: string;
    children: Snippet;
  };
  let { summary, open = false, variant = "plain", class: className, children }: Props = $props();
</script>

<details
  class={cn(
    "group min-w-0",
    variant === "card" && "rounded-md border border-line bg-surface",
    className,
  )}
  {open}
>
  <summary
    class={cn(
      "flex min-h-control cursor-pointer list-none items-center gap-2 rounded-sm font-semibold",
      variant === "card" && "px-4",
    )}
  >
    <ChevronRight
      class="size-4 shrink-0 transition-transform duration-fast group-open:rotate-90"
      aria-hidden="true"
    />
    {summary}
  </summary>
  <div class={cn("min-w-0 pt-2 pb-1", variant === "card" ? "px-4 pb-4" : "ps-6")}>
    {@render children()}
  </div>
</details>

<style>
  /* list-none hides the marker everywhere but Safari, which draws its own. */
  summary::-webkit-details-marker {
    display: none;
  }
  /*
   * The content grows to its height and shrinks back. Closed content is hidden with
   * content-visibility, which transitions discretely so it stays visible while shrinking.
   * Elsewhere, and with reduced motion, it opens and closes at once.
   */
  @supports (interpolate-size: allow-keywords) {
    @media (prefers-reduced-motion: no-preference) {
      details::details-content {
        interpolate-size: allow-keywords;
        overflow: clip;
        /* Room for the focus ring (3px outline, 3px offset) of a control at the edge. */
        overflow-clip-margin: 6px;
        /* Fixed containment, or toggling content-visibility makes the height jitter. */
        contain: layout;
        transition-property: block-size, content-visibility;
        transition-duration: var(--duration-normal);
        transition-timing-function: var(--ease-standard);
        transition-behavior: allow-discrete;
      }
      details:not([open])::details-content {
        block-size: 0;
      }
    }
  }
</style>
