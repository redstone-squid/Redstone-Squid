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
    "[&_:is(p,ul,ol,pre,blockquote,table)]:mt-4 [&>:first-child]:mt-0",
    "[&_h2]:mt-8 [&_h2]:text-xl [&_h2]:font-bold [&_h2]:tracking-tight [&_h3]:mt-6 [&_h3]:text-lg [&_h3]:font-bold",
    "[&_li]:mt-1 [&_ol]:list-decimal [&_ol]:ps-6 [&_ul]:list-disc [&_ul]:ps-6",
    "[&_blockquote]:border-s-4 [&_blockquote]:border-line [&_blockquote]:ps-4 [&_blockquote]:text-fg-muted",
    "[&_code]:rounded-xs [&_code]:bg-surface-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.9em]",
    "[&_pre]:overflow-x-auto [&_pre]:rounded-sm [&_pre]:bg-surface-muted [&_pre]:p-4 [&_pre_code]:bg-transparent [&_pre_code]:p-0",
    "[&_table]:w-full [&_table]:border-collapse [&_td]:border-b [&_td]:border-line [&_td]:py-2 [&_td]:text-start [&_th]:border-b [&_th]:border-line-strong [&_th]:py-2 [&_th]:text-start",
    className,
  )}
>
  {@render children()}
</div>
