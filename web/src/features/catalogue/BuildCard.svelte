<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import * as m from "@/locales/messages.gen";
  import Text from "@/ui/content/Text.svelte";
  import Card from "@/ui/data/Card.svelte";
  import BuildFacts from "./BuildFacts.svelte";
  import BuildPreview from "./BuildPreview.svelte";
  import { buildHref, buildName } from "./presentation";

  /**
   * A public build as a card linking to its page. `category` is the label for its kind;
   * `priority` loads the preview first, for the first card above the fold. `missingFacts`
   * names facts the build lacks for a comparison the card is shown in. `compact` keeps the
   * image, title, and creators, for galleries where the measurements would crowd the grid.
   */
  type Props = {
    build: PublicBuildSummary;
    category: string;
    as?: "article" | "li";
    headingLevel?: 2 | 3;
    priority?: boolean;
    missingFacts?: string[];
    compact?: boolean;
  };
  let {
    build,
    category,
    as = "article",
    headingLevel = 3,
    priority = false,
    missingFacts = [],
    compact = false,
  }: Props = $props();
</script>

<Card
  {as}
  title={buildName(build)}
  href={buildHref(build.id)}
  {headingLevel}
  eyebrow={category}
  padding={compact ? "sm" : "md"}
>
  {#snippet media()}
    <BuildPreview {build} {priority} />
  {/snippet}
  {#if compact}
    <Text size="sm" tone="muted" lines={1}>
      {formatList(build.creators.map((creator) => creator.name))}
    </Text>
  {:else}
    <BuildFacts {build} />
  {/if}
  {#if missingFacts.length > 0}
    <Text size="sm" tone="warning">{m.buildMissingFacts({ facts: formatList(missingFacts) })}</Text>
  {/if}
</Card>
