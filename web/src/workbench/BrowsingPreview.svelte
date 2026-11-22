<script lang="ts">
  import type { Snippet } from "svelte";
  import ArrowLeft from "@lucide/svelte/icons/arrow-left";
  import ArrowRight from "@lucide/svelte/icons/arrow-right";
  import { readOffset } from "@/features/catalogue/pagination";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/svelte/Button.svelte";
  import { setStoryOption, storySearch } from "./story-search";

  // Astro's checker sees children, while its Svelte renderer supplies named snippets.
  // eslint-disable-next-line svelte/no-unused-props -- Astro represents named slots as JSX children.
  let {
    search,
    first,
    next,
  }: {
    search: string;
    children?: unknown;
    first?: Snippet;
    next?: Snippet;
  } = $props();
  const params = $derived($storySearch ?? new URLSearchParams(search));
  const second = $derived(readOffset(params) === 1);
</script>

<div class="grid min-w-0 rounded-lg border border-line bg-surface-muted shadow-card">
  <div aria-live="polite">
    <div hidden={second}>{@render first?.()}</div>
    <div hidden={!second}>{@render next?.()}</div>
  </div>
  <nav class="flex justify-end px-4 py-3" aria-label={m.navPagination()}>
    <Button onclick={() => setStoryOption("offset", second ? null : "1")}>
      {#if second}<ArrowLeft size={16} aria-hidden="true" />{/if}
      {second ? m.commonPrevious() : m.commonLoadMore()}
      {#if !second}<ArrowRight size={16} aria-hidden="true" />{/if}
    </Button>
  </nav>
</div>
