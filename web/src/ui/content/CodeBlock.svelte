<script lang="ts">
  import { cn } from "@/ui/cn";
  import { highlight, type CodeLanguage } from "./highlight";

  /**
   * A block of code, a traceback, or a log, highlighted when `lang` is given. It keeps line
   * breaks and scrolls sideways unless `wrap` is set. Use `Code` inside a line of text.
   *
   * Highlighting runs wherever the block renders, so inside a hydrated island it adds about
   * 70 KiB gzip of highlighter. Render it statically when the island does not need it.
   */
  type Props = {
    code: string;
    lang?: CodeLanguage;
    class?: string;
  } & (
    | { wrap: true; label?: string }
    /** A scrolling block needs a name, so keyboard users can reach and scroll it. */
    | { wrap?: false; label: string }
  );
  let { code, lang, wrap = false, label, class: className }: Props = $props();

  const tokens = $derived(lang === undefined ? [{ content: code }] : highlight(code, lang));
</script>

<!-- A scrolling block must be reachable by keyboard to scroll, so it is a named region. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<pre
  class={cn(
    "min-w-0 rounded-sm border border-line bg-surface-muted p-4 font-mono text-xs",
    wrap ? "wrap-anywhere whitespace-pre-wrap" : "overflow-x-auto",
    className,
  )}
  tabindex={wrap ? undefined : 0}
  role={wrap ? undefined : "region"}
  aria-label={label}><code
    >{#each tokens as token, index (index)}<span class={token.class}>{token.content}</span
      >{/each}</code
  ></pre>
