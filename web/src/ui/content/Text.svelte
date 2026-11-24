<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /** Body text. `numeric` uses tabular figures, for numbers that line up in columns or update. */
  type Props = {
    as?: "p" | "span" | "div";
    size?: "xs" | "sm" | "md";
    tone?: "default" | "muted" | "danger" | "success" | "warning";
    weight?: "normal" | "semibold";
    numeric?: boolean;
    /** Clamp to this many lines with an ellipsis. */
    lines?: 1 | 2 | 3;
    class?: string;
    children: Snippet;
  };
  let {
    as = "p",
    size = "md",
    tone = "default",
    weight = "normal",
    numeric = false,
    lines,
    class: className,
    children,
  }: Props = $props();
  const sizes = { xs: "text-xs", sm: "text-sm", md: "text-base" };
  const tones = {
    default: "text-fg",
    muted: "text-fg-muted",
    danger: "text-danger",
    success: "text-success",
    warning: "text-warning",
  };
  const clamps = { 1: "truncate", 2: "line-clamp-2", 3: "line-clamp-3" };
</script>

<svelte:element
  this={as}
  class={cn(
    "min-w-0 wrap-break-word",
    sizes[size],
    tones[tone],
    weight === "semibold" && "font-semibold",
    numeric && "tabular-nums",
    lines !== undefined && clamps[lines],
    className,
  )}
>
  {@render children()}
</svelte:element>
