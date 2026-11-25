<script lang="ts">
  import X from "@lucide/svelte/icons/x";
  import type { LucideIcon } from "@lucide/svelte";
  import type { HTMLInputAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import type { FieldMessages } from "@/ui/forms/field";
  import FieldFrame from "./FieldFrame.svelte";
  import CharacterCount from "./CharacterCount.svelte";
  import { boxClass, boxInputClass } from "./styles";

  /**
   * A single-line text input. Numbers use `inputmode="numeric"` (or `decimal`) with the
   * default `type="text"`, since `type="number"` changes on scroll and ignores the locale.
   *
   * `prefix` and `suffix` are text beside the value, such as a unit; the suffix also
   * describes the input. `counter` shows the length against `maxlength`. `clearable` adds a
   * button named `clearLabel` that empties the field and fires `input`.
   */
  type Props = Omit<HTMLInputAttributes, "prefix" | "width"> &
    FieldMessages & {
      icon?: LucideIcon;
      prefix?: string;
      suffix?: string;
      counter?: boolean;
      clearable?: boolean;
      clearLabel?: string;
      input?: HTMLInputElement;
    };
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
    icon: Glyph,
    prefix,
    suffix,
    counter = false,
    clearable = false,
    clearLabel,
    value = $bindable(""),
    input = $bindable(),
    maxlength,
    disabled,
    readonly,
    ...props
  }: Props = $props();
  const suffixId = $props.id();
  const text = $derived(String(value ?? ""));

  function clear(): void {
    if (!input) return;
    value = "";
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }
  /** Text beside the input, such as a unit. */
  const adornmentClass = "flex shrink-0 items-center text-fg-muted select-none";
</script>

{#snippet count()}<CharacterCount length={text.length} max={Number(maxlength)} />{/snippet}

<FieldFrame
  aside={counter && maxlength != null ? count : undefined}
  {label}
  {detail}
  {hint}
  {error}
  {labelVisibility}
  {width}
  {id}
  aria-describedby={suffix === undefined
    ? describedBy
    : [describedBy, suffixId].filter(Boolean).join(" ")}
  aria-invalid={invalid}
  class={className}
>
  {#snippet children(attributes)}
    <div
      class={boxClass}
      data-disabled={disabled === true ? "" : undefined}
      data-readonly={readonly === true ? "" : undefined}
    >
      {#if Glyph}<span class={cn(adornmentClass, "ps-3")}
          ><Glyph class="size-4" aria-hidden="true" /></span
        >{/if}
      {#if prefix !== undefined}<span class={cn(adornmentClass, "ps-3")} aria-hidden="true"
          >{prefix}</span
        >{/if}
      <input
        {...props}
        {...attributes}
        {maxlength}
        {disabled}
        {readonly}
        bind:value
        bind:this={input}
        class={cn(boxInputClass, "px-3 py-2")}
      />
      {#if suffix !== undefined}<span id={suffixId} class={cn(adornmentClass, "pe-3")}
          >{suffix}</span
        >{/if}
      {#if clearable && text !== "" && disabled !== true && readonly !== true}<button
          type="button"
          class="grid w-control shrink-0 cursor-pointer place-items-center text-fg-muted hover:text-fg focus-visible:-outline-offset-3"
          aria-label={clearLabel}
          onclick={clear}><X class="size-4" aria-hidden="true" /></button
        >{/if}
    </div>
  {/snippet}
</FieldFrame>
