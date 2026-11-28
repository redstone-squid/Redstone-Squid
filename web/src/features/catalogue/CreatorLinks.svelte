<script lang="ts">
  import type { PublicBuildCreator } from "@/generated/types.gen";
  import { formatListParts } from "@/locales/format";
  import Link from "@/ui/content/Link.svelte";
  import Text from "@/ui/content/Text.svelte";
  import { creatorHref } from "./presentation";

  /** A build's creators as a sentence of links, joined the way the locale writes a list. */
  let { creators }: { creators: Pick<PublicBuildCreator, "name" | "account_id">[] } = $props();
  const parts = $derived.by(() => {
    let next = 0;
    return formatListParts(creators.map((creator) => creator.name)).map((part) =>
      part.type === "element"
        ? { ...part, creator: creators[next++] }
        : { ...part, creator: undefined },
    );
  });
</script>

<Text size="sm" tone="muted">
  {#each parts as part, index (index)}{#if part.creator}<Link
        href={creatorHref(part.creator)}
        variant="subtle">{part.value}</Link
      >{:else}{part.value}{/if}{/each}
</Text>
