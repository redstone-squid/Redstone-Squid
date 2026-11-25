<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import Text from "@/ui/content/Text.svelte";
  import Card from "@/ui/data/Card.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import Image from "@/ui/data/Image.svelte";

  /**
   * A public build as a card linking to its page. `category` is the label for its kind;
   * `priority` loads the preview first, for the first card above the fold.
   */
  type Props = {
    build: PublicBuildSummary;
    category: string;
    as?: "article" | "li";
    headingLevel?: 2 | 3;
    priority?: boolean;
  };
  let { build, category, as = "article", headingLevel = 3, priority = false }: Props = $props();
  const title = $derived(build.display_name ?? build.title);
  const unknown = m.buildDimensionUnknown();
  const { width, height, depth } = $derived(build.dimensions);
  // Only HTTPS and same-origin previews are shown; the API should never send others, but this
  // is untrusted input.
  const preview = $derived.by(() => {
    const url = build.preview?.url;
    if (url === undefined) return null;
    if (url.startsWith("/") && !url.startsWith("//")) return url;
    return URL.canParse(url) && url.startsWith("https://") ? url : null;
  });
</script>

<Card {as} {title} href={localizePath(`/builds/${build.id}`)} {headingLevel} eyebrow={category}>
  {#snippet media()}
    <Image
      src={preview}
      alt={m.buildPreviewAlt({ title })}
      width={640}
      height={400}
      {priority}
      fallbackLabel={preview === null ? m.buildNoPreview() : m.buildImageUnavailable()}
    />
  {/snippet}
  <Text size="sm" tone="muted">{formatList(build.creators.map((creator) => creator.name))}</Text>
  <DescriptionList
    layout="inline"
    size="sm"
    divided
    items={[
      {
        term: m.buildDimensions(),
        value: m.buildSize({
          width: width ?? unknown,
          height: height ?? unknown,
          depth: depth ?? unknown,
        }),
        numeric: true,
      },
      {
        term: m.buildOpening(),
        value:
          build.opening_time === null
            ? m.buildTimingUnknown()
            : m.buildTicks({ ticks: build.opening_time }),
        numeric: true,
      },
    ]}
  />
</Card>
