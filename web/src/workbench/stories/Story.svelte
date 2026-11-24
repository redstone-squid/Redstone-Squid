<script lang="ts">
  import type { Snippet } from "svelte";

  /**
   * One component's story: an anchored heading, the `src/ui` path it shows (`source`, such as
   * "feedback/Badge"), and a preview of its variants and states. The workbench coverage test
   * reads `source` to check that every component has a story.
   */
  type Props = { name: string; source: string; description?: string; children: Snippet };
  let { name, source, description, children }: Props = $props();
</script>

<section
  id={name.toLowerCase()}
  aria-labelledby={`${name.toLowerCase()}-title`}
  class="grid min-w-0 gap-4 rounded-md border border-line bg-surface p-panel shadow-card"
>
  <header class="grid gap-1">
    <h2 id={`${name.toLowerCase()}-title`} class="text-xl font-bold tracking-tight">{name}</h2>
    <code class="font-mono text-xs text-fg-muted"
      >{source.startsWith("features/") ? `src/${source}` : `src/ui/${source}.svelte`}</code
    >
    {#if description}<p class="max-w-prose text-sm text-fg-muted">{description}</p>{/if}
  </header>
  <div class="grid min-w-0 gap-6">{@render children()}</div>
</section>
