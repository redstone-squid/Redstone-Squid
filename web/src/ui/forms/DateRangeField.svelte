<script lang="ts">
  import type { FieldMessages } from "@/ui/forms/field";
  import type { DateLabels } from "./dates";
  import DateField from "./DateField.svelte";
  import FieldGroup from "./FieldGroup.svelte";

  type Bound = { name: string; value: string; label: string; error?: string | null };

  /**
   * A span of dates, such as "submitted between": two `DateField`s in a fieldset, joined by
   * `separator` ("to") and wrapping on narrow screens. Each bound's `label` ("From") names its
   * field for screen readers; the legend is visible. Each calendar disables days on the wrong
   * side of the other bound. Validate in the feature, and leave either empty for an open span.
   */
  type Props = Omit<FieldMessages, "width" | "labelVisibility"> & {
    start: Bound;
    end: Bound;
    separator: string;
    min?: string;
    max?: string;
    disabled?: boolean;
    labels: DateLabels;
    class?: string;
  };
  let {
    start = $bindable(),
    end = $bindable(),
    separator,
    min,
    max,
    disabled,
    labels,
    ...group
  }: Props = $props();
</script>

<FieldGroup {...group} compound>
  <!-- The separator wraps with the end, so a narrow row reads "1 May", then "to 9 May". -->
  <div class="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-2">
    <DateField
      name={start.name}
      label={start.label}
      labelVisibility="hidden"
      error={start.error}
      bind:value={start.value}
      {min}
      max={end.value === "" ? max : end.value}
      {disabled}
      {labels}
    />
    <div class="flex min-w-0 items-start gap-3">
      <span class="flex h-control items-center text-fg-muted" aria-hidden="true">{separator}</span>
      <DateField
        name={end.name}
        label={end.label}
        labelVisibility="hidden"
        error={end.error}
        bind:value={end.value}
        min={start.value === "" ? min : start.value}
        {max}
        {disabled}
        {labels}
      />
    </div>
  </div>
</FieldGroup>
