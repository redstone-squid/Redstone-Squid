<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /**
   * Code, identifiers, and keys. `block` keeps line breaks and scrolls sideways unless `wrap`
   * is set; `key` renders a keyboard key.
   */
  type Props = {
    variant?: "inline" | "block" | "key";
    wrap?: boolean;
    /** Accessible name for a scrolling block, so keyboard users can reach and scroll it. */
    label?: string;
    class?: string;
    children: Snippet;
  };
  let { variant = "inline", wrap = false, label, class: className, children }: Props = $props();
</script>

{#if variant === "block"}
  <!-- A scrolling block must be reachable by keyboard to scroll; it is a named region then. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <pre
    class={cn(
      "min-w-0 rounded-sm border border-line bg-surface-muted p-4 font-mono text-xs",
      wrap ? "wrap-anywhere whitespace-pre-wrap" : "overflow-x-auto",
      className,
    )}
    tabindex={wrap || label === undefined ? undefined : 0}
    role={wrap || label === undefined ? undefined : "region"}
    aria-label={label}><code>{@render children()}</code></pre>
{:else if variant === "key"}
  <kbd
    class={cn(
      "rounded-xs border border-line-strong bg-surface px-1.5 py-0.5 font-mono text-xs shadow-pressed",
      className,
    )}>{@render children()}</kbd
  >
{:else}
  <code
    class={cn(
      "rounded-xs bg-surface-muted px-1 py-0.5 font-mono text-[0.9em] wrap-anywhere",
      className,
    )}>{@render children()}</code
  >
{/if}
