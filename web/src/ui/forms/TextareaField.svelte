<script lang="ts">
  import type { HTMLTextareaAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import type { FieldMessages } from "@/ui/forms/field";
  import { inputClass } from "./styles";
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
      class={cn(inputClass, "min-h-24 resize-y", className)}></textarea>{/snippet}
</FieldFrame>
