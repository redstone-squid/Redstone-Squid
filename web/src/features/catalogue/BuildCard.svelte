<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import Text from "@/ui/content/Text.svelte";
  import Card from "@/ui/data/Card.svelte";
  import BuildFacts from "./BuildFacts.svelte";
  import BuildPreview from "./BuildPreview.svelte";

  /**
   * A public build as a card linking to its page. `category` is the label for its kind;
   * `priority` loads the preview first, for the first card above the fold. `missingFacts`
   * names facts the build lacks for a comparison the card is shown in.
   */
  type Props = {
    build: PublicBuildSummary;
    category: string;
    as?: "article" | "li";
    headingLevel?: 2 | 3;
    priority?: boolean;
    missingFacts?: string[];
  };
  let {
    build,
    category,
    as = "article",
    headingLevel = 3,
    priority = false,
    missingFacts = [],
  }: Props = $props();
  const title = $derived(build.display_name ?? build.title);
</script>

<Card {as} {title} href={localizePath(`/builds/${build.id}`)} {headingLevel} eyebrow={category}>
  {#snippet media()}
    <BuildPreview {build} {priority} />
  {/snippet}
  {#if missingFacts.length > 0}
    <Text size="sm" tone="warning">{m.buildMissingFacts({ facts: formatList(missingFacts) })}</Text>
  {/if}
  <BuildFacts {build} />
</Card>
