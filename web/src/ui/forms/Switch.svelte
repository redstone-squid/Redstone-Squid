<script lang="ts">
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import type { HTMLInputAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import Spinner from "@/ui/feedback/Spinner.svelte";
  import { fieldAttributes, type FieldMessages } from "@/ui/forms/field";
  import { fieldErrorClass, fieldMessageClass, labelClass } from "./styles";

  /**
   * An on/off setting that applies as soon as it changes, such as "Hide my profile". Use
   * `CheckField` for a choice that is saved with the rest of a form.
   *
   * A native checkbox with `role="switch"`, so it submits like a checkbox without
   * JavaScript. While `pending` it stays focusable but ignores clicks and shows a spinner.
   */
  type Props = Omit<HTMLInputAttributes, "type" | "width"> &
    Omit<FieldMessages, "labelVisibility" | "width"> & { pending?: boolean };
  let {
    label,
    detail,
    hint,
    error,
    id,
    "aria-describedby": describedBy,
    "aria-invalid": invalid,
    class: className,
    checked = $bindable(false),
    pending = false,
    onclick,
    ...props
  }: Props = $props();
  const autoId = $props.id();
  const control = $derived(
    fieldAttributes(id ?? autoId, {
      hint,
      error,
      "aria-describedby": describedBy,
      "aria-invalid": invalid,
    }),
  );
</script>

<div class={cn("flex min-w-0 items-start justify-between gap-4 text-sm", className)}>
  <div class="grid min-w-0 gap-1">
    <label class={labelClass} for={control.id}
      >{label}{#if detail !== undefined}
        <span class="font-normal text-fg-muted">({detail})</span>{/if}</label
    >
    {#if hint != null && hint !== ""}<p id={`${control.id}-hint`} class={fieldMessageClass}>
        {hint}
      </p>{/if}
    {#if error != null && error !== ""}<p id={`${control.id}-error`} class={fieldErrorClass}>
        <CircleAlert class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />{error}
      </p>{/if}
  </div>
  <span class="flex shrink-0 items-center gap-2">
    {#if pending}<Spinner />{/if}
    <input
      {...props}
      {...control}
      type="checkbox"
      role="switch"
      {...{ switch: true }}
      bind:checked
      aria-busy={pending ? "true" : undefined}
      aria-disabled={pending ? "true" : undefined}
      onclick={(event) => {
        if (pending) event.preventDefault();
        else onclick?.(event);
      }}
      class={cn(
        "relative h-6 w-10 shrink-0 cursor-pointer appearance-none rounded-full border border-line-strong bg-surface-muted",
        "transition-colors duration-150 ease-standard",
        "before:absolute before:start-0.5 before:top-0.5 before:size-4.5 before:rounded-full before:bg-fg-muted",
        "before:transition-transform before:duration-150 before:ease-standard",
        "checked:border-accent checked:bg-accent checked:before:translate-x-4 checked:before:bg-on-accent",
        "rtl:checked:before:-translate-x-4",
        "disabled:cursor-not-allowed disabled:opacity-50 aria-busy:cursor-progress",
        "aria-invalid:border-danger",
      )}
    />
  </span>
</div>
