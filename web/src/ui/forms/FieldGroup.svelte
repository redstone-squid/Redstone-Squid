<script lang="ts">
  import type { HTMLFieldsetAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import { fieldMessageClass, fieldErrorClass } from "./styles";
  import { fieldAttributes, type FieldMessages } from "@/ui/forms/field";
  let {
    id,
    label,
    detail,
    hint,
    error,
    class: className,
    children,
    ...props
  }: HTMLFieldsetAttributes & FieldMessages = $props();
  const autoId = $props.id();
  const attributes = $derived(fieldAttributes(id ?? autoId, { ...props, hint, error }));
</script>

<fieldset {...props} {...attributes} class={cn("grid min-w-0 content-start gap-2", className)}>
  <legend class="mb-2 text-sm font-semibold"
    >{label}{#if detail}
      <small class="font-normal text-fg-muted">({detail})</small>{/if}</legend
  >
  {@render children?.()}
  {#if hint}<p id={`${attributes.id}-hint`} class={fieldMessageClass}>
      {hint}
    </p>{/if}
  {#if error}<p id={`${attributes.id}-error`} class={fieldErrorClass} role="alert">
      {error}
    </p>{/if}
</fieldset>
