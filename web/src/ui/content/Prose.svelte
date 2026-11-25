<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * Long-form content, such as rendered Markdown passed in from a page. Styles the elements
   * inside it, which cannot carry their own classes.
   */
  type Props = { size?: "sm" | "md"; class?: string; children: Snippet };
  let { size = "md", class: className, children }: Props = $props();
</script>

<div
  class={cn(
    "max-w-prose min-w-0 wrap-break-word",
    size === "sm" ? "text-sm" : "text-base",
    className,
  )}
>
  {@render children()}
</div>

<style>
  /* The content comes from the caller, so every selector below reaches into it. */
  div :global(:is(p, ul, ol, pre, blockquote, table)) {
    margin-block-start: 1rem;
  }
  div :global(h2) {
    margin-block-start: 2rem;
    font-size: var(--text-xl);
    line-height: var(--text-xl--line-height);
    font-weight: 700;
    letter-spacing: var(--tracking-tight);
  }
  div :global(h3) {
    margin-block-start: 1.5rem;
    font-size: var(--text-lg);
    line-height: var(--text-lg--line-height);
    font-weight: 700;
  }
  div > :global(:first-child) {
    margin-block-start: 0;
  }
  div :global(li) {
    margin-block-start: 0.25rem;
  }
  div :global(:is(ul, ol)) {
    padding-inline-start: 1.5rem;
  }
  div :global(ul) {
    list-style-type: disc;
  }
  div :global(ol) {
    list-style-type: decimal;
  }
  div :global(blockquote) {
    border-inline-start: 4px solid var(--color-line);
    padding-inline-start: 1rem;
    color: var(--color-fg-muted);
  }
  div :global(code) {
    border-radius: var(--radius-xs);
    background-color: var(--color-surface-muted);
    padding-inline: 0.25rem;
    font-family: var(--font-mono);
    font-size: 0.9em;
  }
  div :global(pre) {
    overflow-x: auto;
    border-radius: var(--radius-sm);
    background-color: var(--color-surface-muted);
    padding: 1rem;
  }
  div :global(pre code) {
    background-color: transparent;
    padding: 0;
  }
  div :global(table) {
    inline-size: 100%;
    border-collapse: collapse;
  }
  div :global(:is(th, td)) {
    border-block-end: 1px solid var(--color-line);
    padding-block: 0.5rem;
    text-align: start;
  }
  div :global(th) {
    border-color: var(--color-line-strong);
  }
</style>
