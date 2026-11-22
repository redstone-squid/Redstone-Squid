<script lang="ts">
  import type { HTMLFieldsetAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import { fieldAttributes, type FieldMessages } from "@/ui/field";
  import { mutedClass } from "@/ui/variants";
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
  {#if hint}<p id={`${attributes.id}-hint`} class={cn(mutedClass, "text-[0.8125rem]")}>
      {hint}
    </p>{/if}
  {#if error}<p id={`${attributes.id}-error`} class="text-[0.8125rem] text-danger" role="alert">
      {error}
    </p>{/if}
</fieldset>
