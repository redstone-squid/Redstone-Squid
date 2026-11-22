<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/svelte/Button.svelte";
  import { setStoryOption, storySearch } from "./story-search";

  let { search }: { search: string } = $props();
  const params = $derived($storySearch ?? new URLSearchParams(search));
  const active = $derived(params.get("scenario") === "upload-retry");
</script>

<Button
  size="small"
  variant={active ? "primary" : "secondary"}
  aria-pressed={active}
  onclick={() => setStoryOption("scenario", active ? null : "upload-retry")}
>
  {m.trialUploadScenario()}
</Button>
