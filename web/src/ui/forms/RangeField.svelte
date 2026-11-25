<script lang="ts">
  import type { FieldMessages } from "@/ui/forms/field";
  import FieldGroup from "./FieldGroup.svelte";
  import TextField from "./TextField.svelte";

  type Bound = { name: string; value: string; label: string; error?: string | null };

  /**
   * A minimum and a maximum, such as "width 3 to 8 blocks": two numeric text fields in a
   * fieldset, joined by `separator` ("to") and wrapping on narrow screens. Each bound's
   * `label` ("Minimum width") names its input for screen readers; the legend is visible.
   * Validate the bounds in the feature, and leave either empty for an open range.
   */
  type Props = Omit<FieldMessages, "width" | "labelVisibility"> & {
    min: Bound;
    max: Bound;
    separator: string;
    unit?: string;
    inputmode?: "numeric" | "decimal";
    width?: "md" | "sm" | "xs";
    disabled?: boolean;
    class?: string;
  };
  let {
    min = $bindable(),
    max = $bindable(),
    separator,
    unit,
    inputmode = "numeric",
    width = "sm",
    disabled,
    ...group
  }: Props = $props();
</script>

<FieldGroup {...group} compound>
  <!-- The separator wraps with the maximum, so a narrow row reads "3 blocks", then "to 8 blocks". -->
  <div class="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-2">
    <TextField
      {width}
      name={min.name}
      label={min.label}
      labelVisibility="hidden"
      error={min.error}
      bind:value={min.value}
      suffix={unit}
      {inputmode}
      {disabled}
    />
    <div class="flex min-w-0 items-start gap-3">
      <span class="flex h-control items-center text-fg-muted" aria-hidden="true">{separator}</span>
      <TextField
        {width}
        name={max.name}
        label={max.label}
        labelVisibility="hidden"
        error={max.error}
        bind:value={max.value}
        suffix={unit}
        {inputmode}
        {disabled}
      />
    </div>
  </div>
</FieldGroup>
