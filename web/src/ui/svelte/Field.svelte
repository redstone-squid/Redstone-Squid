<script lang="ts">
  import type { HTMLInputAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import type { FieldMessages } from "@/ui/field";
  import { inputClass } from "@/ui/variants";
  import FieldFrame from "./FieldFrame.svelte";
  type Props = HTMLInputAttributes & FieldMessages;
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
  {#snippet children(attributes)}<input
      {...props}
      {...attributes}
      bind:value
      class={cn(inputClass, className)}
    />{/snippet}
</FieldFrame>
