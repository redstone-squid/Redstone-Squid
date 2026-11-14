<script lang="ts">
  import type { HTMLInputAttributes } from "svelte/elements";
  import { cx } from "class-variance-authority";
  type Props = HTMLInputAttributes & { label: string; hint?: string; error?: string };
  let {
    label,
    hint,
    error,
    id: suppliedId,
    class: className,
    "aria-describedby": describedBy,
    "aria-invalid": invalid,
    value = $bindable(""),
    ...props
  }: Props = $props();
  const generatedId = $props.id();
  const id = $derived(suppliedId ?? generatedId);
  const description = $derived(error ?? hint);
  const descriptionIds = $derived(
    [describedBy, description ? `${id}-description` : undefined].filter(Boolean).join(" ") ||
      undefined,
  );
</script>

<div class="sq-field">
  <label for={id}>{label}</label>
  <input
    {...props}
    {id}
    bind:value
    class={cx("sq-input", className)}
    aria-invalid={invalid ?? Boolean(error)}
    aria-describedby={descriptionIds}
  />
  {#if description}<p id={`${id}-description`} class={error ? "sq-error" : "sq-muted"}>
      {description}
    </p>{/if}
</div>
