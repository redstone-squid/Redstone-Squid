<script lang="ts">
  import type { LucideIcon } from "@lucide/svelte";
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
   * Short static metadata such as a category or status. The text carries the meaning; tone
   * and `dot` only reinforce it. Use `Chip` for tags people can click or remove.
   */
  type Props = {
    tone?: keyof typeof tones;
    dot?: boolean;
    icon?: LucideIcon;
    size?: "sm" | "md";
    class?: string;
    children: Snippet;
  };
  let {
    tone = "neutral",
    dot = false,
    icon: Glyph,
    size = "sm",
    class: className,
    children,
  }: Props = $props();
</script>

<span
  class={cn(
    "inline-flex w-fit items-center gap-1.5 rounded-xs font-semibold whitespace-nowrap",
    size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-sm",
    tones[tone],
    className,
  )}
>
  {#if Glyph}<Glyph class="size-[1em] shrink-0" aria-hidden="true" />{:else if dot}<span
      class="size-[0.45em] shrink-0 rounded-full bg-current"
      aria-hidden="true"
    ></span>{/if}{@render children()}
</span>
