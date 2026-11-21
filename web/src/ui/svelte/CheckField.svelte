<script lang="ts">
  import type { HTMLInputAttributes } from "svelte/elements";

  import type { FieldMessages } from "../field";
  import FieldFrame from "./FieldFrame.svelte";
  type Props = HTMLInputAttributes & FieldMessages & { type?: "checkbox" | "radio" };
  let {
    label,
    detail,
    hint,
    error,
    id,
    "aria-describedby": describedBy,
    "aria-invalid": invalid,
    class: className,
    type = "checkbox",
    checked = $bindable(false),
    onchange,
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
  inline
>
  {#snippet children(attributes)}<input
      {...props}
      {...attributes}
      {type}
      {checked}
      onchange={(event) => {
        checked = event.currentTarget.checked;
        onchange?.(event);
      }}
      class={className}
    />{/snippet}
</FieldFrame>
