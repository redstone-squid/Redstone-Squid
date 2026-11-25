<script lang="ts">
  import type { Snippet } from "svelte";

  /**
   * One component's story: an anchored heading, the `src/ui` path it shows (`source`, such as
   * "feedback/Badge", or a stylesheet such as "app.css"), and a preview of its variants and states. The workbench coverage test
   * reads `source` to check that every component has a story.
   */
  type Props = {
    name: string;
    source: string;
    heading?: string;
    description?: string;
    children: Snippet;
  };
  let { name, source, heading = name, description, children }: Props = $props();
  const id = $derived(name.toLowerCase().replaceAll(" ", "-"));
</script>

<section
  {id}
  aria-labelledby={`${id}-title`}
  class="grid min-w-0 gap-4 rounded-md border border-line bg-surface p-panel shadow-card"
>
  <header class="grid gap-1">
    <h2 id={`${id}-title`} class="text-xl font-bold tracking-tight">{heading}</h2>
    <code class="font-mono text-xs wrap-anywhere text-fg-muted"
      >{source.startsWith("features/")
        ? `src/${source}`
        : `src/ui/${source}${source.endsWith(".css") ? "" : ".svelte"}`}</code
    >
    {#if description}<p class="max-w-prose text-sm wrap-anywhere text-fg-muted">
        {description}
      </p>{/if}
  </header>
  <div class="grid min-w-0 gap-6">{@render children()}</div>
</section>
