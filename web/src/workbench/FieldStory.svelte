<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import { layoutStyles } from "@/ui/variants";
  import FormSpecimen from "./FormSpecimen.svelte";
  import StoryOptions from "./StoryOptions.svelte";
  import { selectedOption } from "./story-option";
  import { setStoryOption, storySearch } from "./story-search";

  let { search }: { search: string } = $props();
  const params = $derived($storySearch ?? new URLSearchParams(search));
  const options = {
    normal: m.workbenchNormal,
    invalid: m.workbenchInvalid,
    disabled: m.workbenchDisabled,
  } as const;
  const state = $derived(selectedOption(options, params, "fieldState", "normal"));
</script>

<div class={layoutStyles({ pattern: "stack" })}>
  <StoryOptions
    label={m.workbenchFieldState()}
    selected={state}
    {options}
    onSelect={(value) => setStoryOption("fieldState", value)}
  />
  <FormSpecimen {state} />
</div>
