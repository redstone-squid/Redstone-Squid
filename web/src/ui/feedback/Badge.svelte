<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  const tones = {
    neutral: "bg-surface-muted text-fg-muted",
    info: "bg-info-soft text-info",
    success: "bg-success-soft text-success",
    warning: "bg-warning-soft text-warning",
    danger: "bg-danger-soft text-danger",
    accent: "bg-accent-soft text-accent",
  };
  /**
   * A state the reader acts on, such as pending, approved, or an unread count; at most one per
   * item. The text carries the meaning; tone and `dot` only reinforce it. Categories and
   * versions are plain text, and tags people can click are `Chip`s.
   */
  type Props = {
    tone?: keyof typeof tones;
    dot?: boolean;
    size?: "sm" | "md";
    class?: string;
    children: Snippet;
  };
  let { tone = "neutral", dot = false, size = "sm", class: className, children }: Props = $props();
</script>

<span
  class={cn(
    "inline-flex w-fit items-center gap-1.5 rounded-xs font-semibold whitespace-nowrap",
    // Forced colors drop the tinted background; an outline keeps the badge's shape.
    "forced-colors:outline",
    size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-sm",
    tones[tone],
    className,
  )}
>
  {#if dot}<span class="size-[0.45em] shrink-0 rounded-full bg-current" aria-hidden="true"
    ></span>{/if}{@render children()}
</span>
