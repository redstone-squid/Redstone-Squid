<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import { layoutStyles } from "@/ui/variants";
  import StoryOptions from "./StoryOptions.svelte";
  import { selectedOption } from "./story-option";
  import { setStoryOption, storySearch } from "./story-search";
  import { buildFixtures } from "./fixtures";

  let { search }: { search: string } = $props();
  const params = $derived($storySearch ?? new URLSearchParams(search));
  const patterns = { stack: m.workbenchStack, row: m.workbenchRow, grid: m.workbenchGrid } as const;
  const gaps = {
    compact: m.workbenchCompact,
    normal: m.workbenchNormal,
    section: m.workbenchSection,
  } as const;
  const pattern = $derived(selectedOption(patterns, params, "layout", "grid"));
  const gap = $derived(selectedOption(gaps, params, "gap", "normal"));
  const { creator, dimensions, timing } = buildFixtures[0];
</script>

<div class="flex flex-wrap items-center justify-between gap-3">
  <StoryOptions
    label={m.workbenchLayouts()}
    selected={pattern}
    options={patterns}
    onSelect={(value) => setStoryOption("layout", value)}
  />
  <StoryOptions
    label={m.workbenchSpacing()}
    selected={gap}
    options={gaps}
    onSelect={(value) => setStoryOption("gap", value)}
  />
</div>
<div class="mt-4 mb-2 rounded-md border border-line bg-canvas p-4">
  <div class={layoutStyles({ pattern, gap })}>
    {#each [[m.workbenchCreator(), creator], [m.workbenchDimensions(), dimensions], [m.workbenchOpening(), timing]] as [term, value] (term)}
      <dl class="rounded-sm border border-line bg-surface p-3">
        <dt class="text-xs text-fg-muted">{term}</dt>
        <dd class="mt-1">{value}</dd>
      </dl>
    {/each}
  </div>
</div>
<code class="text-xs text-fg-muted">{`<Layout pattern="${pattern}" gap="${gap}">`}</code>
