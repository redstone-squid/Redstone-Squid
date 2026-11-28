<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import * as m from "@/locales/messages.gen";
  import Text from "@/ui/content/Text.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import { buildSize, ticks } from "./presentation";

  let { build }: { build: PublicBuildSummary } = $props();
</script>

<Text size="sm" tone="muted">{formatList(build.creators.map((creator) => creator.name))}</Text>
<DescriptionList
  layout="inline"
  size="sm"
  items={[
    { term: m.buildDimensions(), value: buildSize(build.dimensions), numeric: true },
    { term: m.buildOpening(), value: ticks(build.opening_time), numeric: true },
  ]}
/>
