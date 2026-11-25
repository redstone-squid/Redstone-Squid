<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  /** Code, identifiers, and keys inside a line of text; `key` renders a keyboard key. Use `CodeBlock` for a block. */
  type Props = {
    variant?: "inline" | "key";
    class?: string;
    children: Snippet;
  };
  let { variant = "inline", class: className, children }: Props = $props();
</script>

{#if variant === "key"}
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
