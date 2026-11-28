<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import * as m from "@/locales/messages.gen";
  import MetaList from "@/ui/content/MetaList.svelte";
  import Text from "@/ui/content/Text.svelte";
  import ListItem from "@/ui/data/ListItem.svelte";
  import BuildPreview from "./BuildPreview.svelte";
  import CreatorLinks from "./CreatorLinks.svelte";
  import { buildHref, buildName, buildSize } from "./presentation";

  /**
   * A build as one row of a `List`: thumbnail, title, linked creators, and its size and
   * opening time. Use it for search results, a creator's builds, and builds beside a record.
   * `missingFacts` names facts the build lacks for the comparison it is listed in.
   */
  type Props = {
    build: PublicBuildSummary;
    missingFacts?: string[];
    density?: "comfortable" | "compact";
  };
  let { build, missingFacts = [], density = "comfortable" }: Props = $props();
  const facts = $derived([
    buildSize(build.dimensions),
    ...(build.opening_time === null ? [] : [m.buildOpensIn({ ticks: build.opening_time })]),
  ]);
</script>

<ListItem title={buildName(build)} href={buildHref(build.id)} {density}>
  {#snippet leading()}
    <BuildPreview
      {build}
      thumbnail={density === "compact" ? "square" : "wide"}
      class={density === "compact" ? "size-10 rounded-xs" : "w-24 rounded-xs"}
    />
  {/snippet}
  {#if build.creators.length > 0}<CreatorLinks creators={build.creators} />{/if}
  {#if density === "comfortable"}<MetaList size="xs" items={facts} />{/if}
  {#if missingFacts.length > 0}
    <Text size="sm" tone="warning">{m.buildMissingFacts({ facts: formatList(missingFacts) })}</Text>
  {/if}
</ListItem>
