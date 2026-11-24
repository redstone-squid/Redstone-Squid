<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * The buttons that end a form. `primary` sits at the end with `secondary` before it, and
   * `destructive` at the start, set apart; on narrow screens they stack with the primary
   * action on top. `note` is a line above them, such as "Publishing is final". `sticky`
   * keeps the row at the bottom of the viewport on long forms.
   */
  type Props = {
    primary: Snippet;
    secondary?: Snippet;
    destructive?: Snippet;
    note?: string;
    sticky?: boolean;
    class?: string;
  };
  let { primary, secondary, destructive, note, sticky = false, class: className }: Props = $props();
</script>

<div
  class={cn(
    "grid min-w-0 gap-3",
    sticky && "sticky bottom-0 z-sticky -mx-1 border-t border-line bg-surface px-1 py-3",
    className,
  )}
>
  {#if note !== undefined}<p class="text-sm text-fg-muted">{note}</p>{/if}
  <div class="flex flex-col-reverse gap-3 sm:flex-row sm:flex-wrap sm:items-center">
    {#if destructive}<div class="flex flex-col sm:me-auto">{@render destructive()}</div>{/if}
    <div class="flex flex-col-reverse gap-3 sm:ms-auto sm:flex-row sm:items-center">
      {@render secondary?.()}
      {@render primary()}
    </div>
  </div>
</div>
