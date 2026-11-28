<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import * as m from "@/locales/messages.gen";
  import Text from "@/ui/content/Text.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  let { build }: { build: PublicBuildSummary } = $props();
  const unknown = m.buildDimensionUnknown();
  const { width, height, depth } = $derived(build.dimensions);
</script>

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
