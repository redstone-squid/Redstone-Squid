<script lang="ts">
  import Search from "@lucide/svelte/icons/search";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import Autocomplete from "@/ui/forms/Autocomplete.svelte";
  import type { SuggestionSource } from "@/ui/forms/field";
  import TextField from "@/ui/forms/TextField.svelte";

  /**
   * A search form that submits `name` (default `q`) to `action` with GET, so results have a
   * URL and it works without JavaScript. `hidden` keeps other query parameters, such as the
   * scope and view, across a new search. `suggestions` turns the field into an
   * `Autocomplete`. `compact` hides the label and uses a single field for the header. Enter submits it. Once hydrated, a button named `clearLabel` empties the field; it does
   * not search again, since `ActiveFilters` removes an applied query.
   */
  type Props = {
    action: string;
    name?: string;
    value?: string;
    label: string;
    placeholder?: string;
    submitLabel: string;
    clearLabel: string;
    hidden?: [string, string][];
    suggestions?: SuggestionSource;
    compact?: boolean;
    class?: string;
  };
  let {
    action,
    name = "q",
    value = "",
    label,
    placeholder,
    submitLabel,
    clearLabel,
    hidden = [],
    suggestions,
    compact = false,
    class: className,
  }: Props = $props();
  const labelVisibility = $derived(compact ? "hidden" : "visible");
</script>

<search class={cn("block min-w-0", className)}>
  <form method="get" {action} class="flex min-w-0 items-end gap-2">
    {#each hidden as [key, hiddenValue], index (index)}
      <input type="hidden" name={key} value={hiddenValue} />
    {/each}
    {#if suggestions}
      <Autocomplete
        class="min-w-0 flex-1"
        {name}
        {value}
        {label}
        {labelVisibility}
        {placeholder}
        load={suggestions.load}
        labels={suggestions.labels}
        clearable
        {clearLabel}
        icon={Search}
      />
    {:else}
      <TextField
        class="min-w-0 flex-1"
        type="search"
        enterkeyhint="search"
        icon={Search}
        clearable
        {clearLabel}
        {name}
        {value}
        {label}
        {labelVisibility}
        {placeholder}
      />
    {/if}
    {#if !compact}<Button type="submit" variant="primary">{submitLabel}</Button>{/if}
  </form>
</search>
