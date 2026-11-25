<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";
  import Heading from "@/ui/content/Heading.svelte";

  /**
   * A bordered surface for one item. With `title` and `href`, the title link covers the whole
   * card, so the card is clickable while screen readers hear only the title; links and buttons
   * inside the card stay clickable on top of it. Without `href` it is a plain surface.
   *
   * `headingLevel` follows the heading the card sits under: 2 directly under the page title.
   */
  type Props = {
    as?: "article" | "li" | "div";
    title?: string;
    href?: string;
    headingLevel?: 2 | 3 | 4;
    eyebrow?: string;
    padding?: "sm" | "md" | "lg";
    tone?: "default" | "muted" | "selected" | "warning";
    media?: Snippet;
    footer?: Snippet;
    class?: string;
    children?: Snippet;
  };
  let {
    as = "article",
    title,
    href,
    headingLevel = 3,
    eyebrow,
    padding = "md",
    tone = "default",
    media,
    footer,
    class: className,
    children,
  }: Props = $props();
  const paddings = { sm: "p-3", md: "p-4 @sm:p-5", lg: "p-5 @sm:p-6" };
  const tones = {
    default: "border-line bg-surface",
    muted: "border-line bg-surface-muted",
    selected: "border-accent bg-accent-soft",
    warning: "border-warning bg-warning-soft",
  };
</script>

<svelte:element
  this={as}
  class={cn(
    "@container relative isolate flex min-w-0 flex-col overflow-hidden rounded-md border shadow-card print:break-inside-avoid",
    href !== undefined &&
      "transition-[border-color] duration-fast hover:border-line-strong has-[[data-card-link]:focus-visible]:outline-3 has-[[data-card-link]:focus-visible]:outline-offset-3 has-[[data-card-link]:focus-visible]:outline-focus",
    tones[tone],
    className,
  )}
>
  {#if media}<div class="border-b border-line">{@render media()}</div>{/if}
  <div
    data-card-body
    class={cn("grid min-w-0 flex-1 content-start gap-2 wrap-break-word", paddings[padding])}
  >
    {#if title !== undefined}
      <Heading level={headingLevel} size="md" {eyebrow}>
        {#if href !== undefined}<a
            {href}
            data-card-link
            class="no-underline after:absolute after:inset-0 hover:underline focus-visible:outline-none"
            >{title}</a
          >{:else}{title}{/if}
      </Heading>
    {/if}
    {@render children?.()}
  </div>
  {#if footer}<div class={cn("border-t border-line", paddings[padding])}>
      {@render footer()}
    </div>{/if}
</svelte:element>

<style>
  /* The title link stretches over the card; other controls in the body stay clickable on top. */
  [data-card-body] :global(:is(a, button, input, select, textarea, summary):not([data-card-link])) {
    position: relative;
    z-index: var(--z-sticky);
  }
</style>
