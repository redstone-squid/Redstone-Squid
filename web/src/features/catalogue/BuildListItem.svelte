<script lang="ts">
  import type { PublicBuildSummary } from "@/generated/types.gen";
  import { formatList } from "@/locales/format";
  import { localizePath } from "@/locales/locale";
  import ListItem from "@/ui/data/ListItem.svelte";

  let { build }: { build: PublicBuildSummary } = $props();
</script>

<ListItem
  density="compact"
  title={build.display_name ?? build.title}
  href={localizePath(`/builds/${build.id}`)}
  description={formatList(build.creators.map((creator) => creator.name))}
/>
