<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * A heading whose `level` sets the document outline and `size` sets how it looks, so a card
   * title can be an `h2` without looking like a section title. `eyebrow` is a short line
   * above, such as a category, grouped with the heading in an `hgroup`. `focusTarget` lets
   * scripts move focus here after replacing the page content.
   */
  type Props = {
    level: 1 | 2 | 3 | 4 | 5 | 6;
    size?: "display" | "xl" | "lg" | "md" | "sm";
    eyebrow?: string;
    id?: string;
    focusTarget?: boolean;
    class?: string;
    children: Snippet;
  };
  let {
    level,
    size,
    eyebrow,
    id,
    focusTarget = false,
    class: className,
    children,
  }: Props = $props();
  const DEFAULT_SIZES = { 1: "xl", 2: "lg", 3: "md", 4: "sm", 5: "sm", 6: "sm" } as const;
  const sizes = {
    display: "text-display tracking-display",
    xl: "text-3xl tracking-tight",
    lg: "text-xl tracking-tight",
    md: "text-lg",
    sm: "text-base",
  };
  const headingClass = $derived(
    cn("font-bold wrap-break-word", sizes[size ?? DEFAULT_SIZES[level]], className),
  );
</script>

{#snippet heading()}
  <svelte:element
    this={`h${level}`}
    {id}
    class={headingClass}
    tabindex={focusTarget ? -1 : undefined}
  >
    {@render children()}
  </svelte:element>
{/snippet}

{#if eyebrow}
  <hgroup class="grid gap-1">
    <p class="text-sm font-semibold text-fg-muted">{eyebrow}</p>
    {@render heading()}
  </hgroup>
{:else}
  {@render heading()}
{/if}
