<script lang="ts">
  import type { HTMLInputAttributes } from "svelte/elements";
  import type { FieldMessages } from "@/ui/forms/field";
  import FieldFrame from "./FieldFrame.svelte";
  import { checkboxClass } from "./styles";

  /** One checkbox with its label, and an optional `hint` describing what it changes. */
  type Props = Omit<HTMLInputAttributes, "type" | "width"> &
    Omit<FieldMessages, "labelVisibility" | "width">;
  let {
    label,
    detail,
    hint,
    error,
    id,
    "aria-describedby": describedBy,
    "aria-invalid": invalid,
    class: className,
    checked = $bindable(false),
    ...props
  }: Props = $props();
</script>

<FieldFrame
  {label}
  {detail}
  {hint}
  {error}
  {id}
  aria-describedby={describedBy}
  aria-invalid={invalid}
  class={className}
  inline
>
  {#snippet children(attributes)}<input
      {...props}
      {...attributes}
      type="checkbox"
      bind:checked
      class={checkboxClass}
    />{/snippet}
</FieldFrame>
