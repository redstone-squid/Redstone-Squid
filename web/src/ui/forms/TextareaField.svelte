<script lang="ts">
  import type { HTMLTextareaAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import type { FieldMessages } from "@/ui/forms/field";
  import CharacterCount from "./CharacterCount.svelte";
  import FieldFrame from "./FieldFrame.svelte";
  import { inputClass } from "./styles";

  /**
   * Multi-line text. `autosize` grows the box with its content up to about 16 lines, where
   * browsers support `field-sizing`; elsewhere it stays resizable. `counter` shows the length
   * against `maxlength`.
   */
  type Props = Omit<HTMLTextareaAttributes, "width"> &
    FieldMessages & { counter?: boolean; autosize?: boolean };
  let {
    label,
    detail,
    hint,
    error,
    labelVisibility,
    width,
    id,
    "aria-describedby": describedBy,
    "aria-invalid": invalid,
    class: className,
    counter = false,
    autosize = false,
    maxlength,
    value = $bindable(""),
    ...props
  }: Props = $props();
</script>

{#snippet count()}<CharacterCount
    length={String(value ?? "").length}
    max={Number(maxlength)}
  />{/snippet}

<FieldFrame
  aside={counter && maxlength != null ? count : undefined}
  {label}
  {detail}
  {hint}
  {error}
  {labelVisibility}
  {width}
  {id}
  aria-describedby={describedBy}
  aria-invalid={invalid}
  class={className}
>
  {#snippet children(attributes)}<textarea
      {...props}
      {...attributes}
      {maxlength}
      bind:value
      class={cn(
        inputClass,
        "block min-h-24 w-full resize-y px-3",
        autosize && "field-sizing-content max-h-96",
      )}></textarea>{/snippet}
</FieldFrame>
