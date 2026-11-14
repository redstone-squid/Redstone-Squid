<script lang="ts">
  import Autocomplete from "../../ui/svelte/Autocomplete.svelte";
  import { translate, type Locale } from "../../lib/i18n";
  import { loadSuggestions } from "./suggestions";
  let {
    locale,
    source,
    name,
    label,
    value = $bindable(""),
    multiline = false,
  }: {
    locale: Locale;
    source: string;
    name: string;
    label: string;
    value?: string;
    multiline?: boolean;
  } = $props();
</script>

<Autocomplete
  {name}
  {label}
  bind:value
  {multiline}
  maxLength={multiline ? 1000 : undefined}
  minLength={multiline ? 0 : 2}
  suggestionsLabel={translate(locale, "search.suggestions")}
  errorLabel={translate(locale, "search.suggestionError")}
  load={(query, cursor, signal) => loadSuggestions(locale, source, query, cursor, signal)}
/>
