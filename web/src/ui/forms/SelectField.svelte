<script lang="ts">
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import type { HTMLSelectAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import type { FieldMessages, SelectOption } from "@/ui/forms/field";
  import FieldFrame from "./FieldFrame.svelte";
  import { inputClass } from "./styles";

  /**
   * A native select, from `options` or `<option>` children. `placeholder` adds a first
   * option with an empty value, such as "Choose a version", which a required select rejects.
   */
  type Props = Omit<HTMLSelectAttributes, "width"> &
    FieldMessages & { options?: SelectOption[]; placeholder?: string };
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
    options,
    placeholder,
    value = $bindable(),
    children: optionChildren,
    ...props
  }: Props = $props();
</script>

<FieldFrame
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
  {#snippet children(attributes)}
    <div class="relative">
      <select
        {...props}
        {...attributes}
        bind:value
        class={cn(
          inputClass,
          "min-h-control w-full cursor-pointer appearance-none ps-3 pe-10 disabled:cursor-not-allowed",
        )}
      >
        {#if placeholder !== undefined}<option value="">{placeholder}</option>{/if}
        {#each options ?? [] as option (option.value)}<option
            value={option.value}
            disabled={option.disabled}>{option.label}</option
          >{/each}
        {@render optionChildren?.()}
      </select>
      <ChevronDown
        class="pointer-events-none absolute end-3 top-1/2 size-4 -translate-y-1/2 text-fg-muted"
        aria-hidden="true"
      />
    </div>
  {/snippet}
</FieldFrame>
