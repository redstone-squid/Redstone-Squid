<script lang="ts">
  import type { StoryOptionLabels } from "./story-option";

  let {
    label,
    selected,
    options,
    onSelect,
  }: {
    label: string;
    selected: string;
    options: StoryOptionLabels<string>;
    onSelect: (value: string) => void;
  } = $props();
</script>

<div
  class="flex w-fit max-w-full flex-wrap gap-1 rounded-md bg-surface-muted p-1"
  role="group"
  aria-label={label}
>
  {#each Object.entries(options) as [option, optionLabel] (option)}
    <button
      type="button"
      class="inline-flex min-h-9 cursor-pointer items-center rounded-sm border border-transparent px-3 text-[0.8125rem] hover:underline aria-pressed:border-line aria-pressed:bg-surface aria-pressed:font-[650] forced-colors:aria-pressed:border-[Highlight]"
      aria-pressed={option === selected}
      onclick={() => onSelect(option)}
    >
      {optionLabel()}
    </button>
  {/each}
</div>
