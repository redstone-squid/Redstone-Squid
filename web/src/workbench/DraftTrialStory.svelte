<script lang="ts">
  import DraftTrial from "./DraftTrial.svelte";
  import { storySearch } from "./story-search";

  let { search }: { search: string } = $props();
  const params = $derived($storySearch ?? new URLSearchParams(search));
  const selected = $derived(params.get("scenario"));
  const scenario = $derived(
    selected === "retry" || selected === "upload-retry" ? selected : undefined,
  );
</script>

<DraftTrial {scenario} />
