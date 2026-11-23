<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * A list of `ListItem` rows, such as notifications, devices, or drafts. Name it with `label`
   * when no heading right above it does.
   */
  type Props = {
    as?: "ul" | "ol";
    label?: string;
    divided?: boolean;
    surface?: boolean;
    class?: string;
    children: Snippet;
  };
  let {
    as = "ul",
    label,
    divided = true,
    surface = true,
    class: className,
    children,
  }: Props = $props();
</script>

<svelte:element
  this={as}
  aria-label={label}
  class={cn(
    "grid min-w-0 list-none",
    divided && "divide-y divide-line",
    surface && "overflow-hidden rounded-md border border-line bg-surface",
    className,
  )}
>
  {@render children()}
</svelte:element>
