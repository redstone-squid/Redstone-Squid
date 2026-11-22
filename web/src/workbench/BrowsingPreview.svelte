<script lang="ts">
  import type { Snippet } from "svelte";
  import { readOffset } from "@/features/catalogue/pagination";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/svelte/Button.svelte";
  import { layoutStyles } from "@/ui/variants";
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

<div class={layoutStyles({ pattern: "stack", gap: "normal" })}>
  <div aria-live="polite">
    <div hidden={second}>{@render first?.()}</div>
    <div hidden={!second}>{@render next?.()}</div>
  </div>
  <nav class="flex justify-between gap-4" aria-label={m.navPagination()}>
    <Button onclick={() => setStoryOption("offset", second ? null : "1")}>
      {second ? m.commonPrevious() : m.commonLoadMore()}
    </Button>
  </nav>
</div>
