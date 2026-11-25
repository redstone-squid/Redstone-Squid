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
   * `Autocomplete`. `compact` hides the label and shortens the button to an icon, for the
   * header; the label still names the field. One magnifier shows either way: in the field, or
   * on the compact button. Once hydrated, a button named `clearLabel` empties the field; it does
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
    hidden?: Record<string, string>;
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
    hidden = {},
    suggestions,
    compact = false,
    class: className,
  }: Props = $props();
  const labelVisibility = $derived(compact ? "hidden" : "visible");
</script>

<search class={cn("block min-w-0", className)}>
  <form method="get" {action} class="flex min-w-0 items-end gap-2">
    {#each Object.entries(hidden) as [key, hiddenValue] (key)}
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
      />
    {:else}
      <TextField
        class="min-w-0 flex-1"
        type="search"
        enterkeyhint="search"
        icon={compact ? undefined : Search}
        clearable
        {clearLabel}
        {name}
        {value}
        {label}
        {labelVisibility}
        {placeholder}
      />
    {/if}
    <Button
      type="submit"
      variant={compact ? "secondary" : "primary"}
      icon={compact ? Search : undefined}
      iconOnly={compact}
      label={submitLabel}>{submitLabel}</Button
    >
  </form>
</search>
