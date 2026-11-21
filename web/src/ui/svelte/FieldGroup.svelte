<script lang="ts">
  import type { HTMLFieldsetAttributes } from "svelte/elements";
  import { cx } from "class-variance-authority";
  import { fieldAttributes, type FieldMessages } from "../field";
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

<fieldset {...props} {...attributes} class={cx("sq-field-group", className)}>
  <legend
    >{label}{#if detail}
      <small>({detail})</small>{/if}</legend
  >
  {@render children?.()}
  {#if hint}<p id={`${attributes.id}-hint`} class="sq-muted">{hint}</p>{/if}
  {#if error}<p id={`${attributes.id}-error`} class="sq-error" role="alert">{error}</p>{/if}
</fieldset>
