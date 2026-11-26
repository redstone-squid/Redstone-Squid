<script lang="ts">
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import type { Snippet } from "svelte";
  import type { ClassValue, HTMLAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import { fieldAttributes, type FieldMessages } from "@/ui/forms/field";
  import { fieldErrorClass, fieldMessageClass, labelClass } from "./styles";

  /**
   * The label, hint and error around one control. `children` receives the control's id,
   * description and invalid state. A control `<label for>` cannot name, such as a group of
   * date segments, takes `aria-labelledby` from the label's id: the control's id plus `-label`. The error is plain text, not an alert: `ErrorSummary`
   * announces a failed submit, and focusing the field reads its error.
   *
   * `inline` puts the control before its label, for checkboxes. `aside` sits at the end of
   * the hint row, such as a character counter. `readout` is live text derived from the value,
   * such as unit conversions, on its own line under the hint.
   */
  type Props = FieldMessages &
    Pick<HTMLAttributes<HTMLElement>, "id" | "aria-describedby" | "aria-invalid"> & {
      inline?: boolean;
      aside?: Snippet;
      readout?: Snippet;
      class?: ClassValue | null;
      children: Snippet<[ReturnType<typeof fieldAttributes>]>;
    };
  let {
    label,
    detail,
    hint,
    error,
    labelVisibility = "visible",
    width = "full",
    inline,
    aside,
    readout,
    class: className,
    children,
    id,
    ...aria
  }: Props = $props();
  const autoId = $props.id();
  const control = $derived(fieldAttributes(id ?? autoId, { hint, error, ...aria }));
  const hasHint = $derived(hint != null && hint !== "");
  const widths = { full: "", md: "max-w-field-md", sm: "max-w-field-sm", xs: "max-w-field-xs" };
</script>

{#snippet caption()}{label}{#if detail !== undefined}
    <span class="ms-1 font-normal text-fg-muted">({detail})</span>{/if}{/snippet}
<div class={cn("grid min-w-0 content-start gap-1.5 text-sm", className)}>
  {#if inline}
    <label class="flex items-start gap-2.5" for={control.id}
      >{@render children(control)}<span class={labelClass}>{@render caption()}</span></label
    >
  {:else}
    <label
      id={`${control.id}-label`}
      class={cn(labelClass, labelVisibility === "hidden" && "sr-only")}
      for={control.id}>{@render caption()}</label
    >
    <div class={cn("relative min-w-0", widths[width])}>{@render children(control)}</div>
  {/if}
  {#if hasHint || aside}
    <div
      class={cn(
        "flex flex-wrap items-start justify-between gap-x-3 gap-y-1",
        inline ? "ps-7" : width !== "full" && "max-w-field-md",
      )}
    >
      {#if hasHint}<p id={`${control.id}-hint`} class={fieldMessageClass}>{hint}</p>{/if}
      {#if aside}<div class="ms-auto min-w-0">{@render aside()}</div>{/if}
    </div>
  {/if}
  {#if readout}{@render readout()}{/if}
  {#if error != null && error !== ""}<p
      id={`${control.id}-error`}
      class={cn(fieldErrorClass, inline && "ps-7")}
    >
      <CircleAlert class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />{error}
    </p>{/if}
</div>
