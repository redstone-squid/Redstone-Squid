<script lang="ts">
  import type { HTMLSelectAttributes } from "svelte/elements";
  import { cx } from "class-variance-authority";
  import type { FieldMessages } from "../field";
  import FieldFrame from "./FieldFrame.svelte";
  type Props = HTMLSelectAttributes & FieldMessages;
  let {
    label,
    detail,
    hint,
    error,
    id,
    "aria-describedby": describedBy,
    "aria-invalid": invalid,
    class: className,
    value = $bindable(),
    children: options,
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
>
  {#snippet children(attributes)}<select
      {...props}
      {...attributes}
      bind:value
      class={cx("sq-input", className)}>{@render options?.()}</select
    >{/snippet}
</FieldFrame>
