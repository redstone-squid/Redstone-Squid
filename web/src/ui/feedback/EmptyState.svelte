<script lang="ts">
  import Inbox from "@lucide/svelte/icons/inbox";
  import type { LucideIcon } from "@lucide/svelte";
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * What to show instead of an empty list: what would be here, and what to do next, such as
   * "Submit a build" or "Remove a filter". Never leave a list or table empty without one.
   */
  type Props = {
    title: string;
    description?: string;
    icon?: LucideIcon;
    size?: "inline" | "page";
    actions?: Snippet;
    class?: string;
  };
  let {
    title,
    description,
    icon: Glyph = Inbox,
    size = "inline",
    actions,
    class: className,
  }: Props = $props();
</script>

<div
  class={cn(
    "grid justify-items-center gap-2 rounded-md border border-dashed border-line text-center",
    size === "page" ? "px-6 py-16" : "px-4 py-8",
    className,
  )}
>
  <Glyph class={cn("text-fg-muted", size === "page" ? "size-10" : "size-7")} aria-hidden="true" />
  <p class={cn("font-semibold text-fg", size === "page" ? "text-lg" : "text-base")}>{title}</p>
  {#if description}<p class="max-w-prose text-sm text-fg-muted">{description}</p>{/if}
  {#if actions}<div class="mt-2 flex flex-wrap justify-center gap-3">{@render actions()}</div>{/if}
</div>
