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
      "flex min-h-control cursor-pointer list-none items-center gap-2 rounded-sm font-semibold [&::-webkit-details-marker]:hidden",
      variant === "card" && "px-4",
    )}
  >
    <ChevronRight
      class="size-4 shrink-0 transition-transform duration-150 group-open:rotate-90"
      aria-hidden="true"
    />
    {summary}
  </summary>
  <div class={cn("min-w-0 pt-2 pb-1", variant === "card" ? "px-4 pb-4" : "ps-6")}>
    {@render children()}
  </div>
</details>
