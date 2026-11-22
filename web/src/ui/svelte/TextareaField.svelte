<script lang="ts">
  import type { HTMLTextareaAttributes } from "svelte/elements";
  import { cx } from "class-variance-authority";
  import type { FieldMessages } from "@/ui/field";
  import FieldFrame from "./FieldFrame.svelte";
  type Props = HTMLTextareaAttributes & FieldMessages;
  let {
    label,
    detail,
    hint,
    error,
    id,
    "aria-describedby": describedBy,
    "aria-invalid": invalid,
    class: className,
    value = $bindable(""),
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
  {#snippet children(attributes)}<textarea
      {...props}
      {...attributes}
      bind:value
      class={cx("sq-input sq-textarea", className)}></textarea>{/snippet}
</FieldFrame>
