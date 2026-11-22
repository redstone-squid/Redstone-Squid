<script lang="ts">
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/svelte/Button.svelte";
  import { parseTheme, THEMES, type Theme } from "@/ui/themes";
  import { setStoryOption, storySearch } from "./story-search";

  let { theme }: { theme: Theme } = $props();
  const current = $derived(
    $storySearch === undefined ? theme : parseTheme($storySearch.get("theme")),
  );
  const next = $derived(THEMES[(THEMES.indexOf(current) + 1) % THEMES.length] ?? current);
  const labels = { light: m.workbenchLightTheme, dark: m.workbenchDarkTheme };

  $effect(() => {
    document.documentElement.dataset.theme = current;
  });
</script>

<Button onclick={() => setStoryOption("theme", next)}>{labels[next]()}</Button>
