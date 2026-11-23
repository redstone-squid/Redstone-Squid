<script lang="ts">
  import type { HTMLSelectAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import type { FieldMessages } from "@/ui/forms/field";
  import { inputClass } from "./styles";
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
      class={cn(inputClass, className)}>{@render options?.()}</select
    >{/snippet}
</FieldFrame>
