<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import { fieldAttributes, type FieldMessages } from "@/ui/field";
  type Props = FieldMessages &
    Pick<HTMLAttributes<HTMLElement>, "id" | "aria-describedby" | "aria-invalid"> & {
      inline?: boolean;
      children: Snippet<[ReturnType<typeof fieldAttributes>]>;
    };
  let { label, detail, hint, error, inline, children, id, ...aria }: Props = $props();
  const autoId = $props.id();
  const control = $derived(fieldAttributes(id ?? autoId, { hint, error, ...aria }));
</script>

{#snippet caption()}{label}{#if detail}
    <small>({detail})</small>{/if}{/snippet}
<div class="sq-field">
  {#if inline}
    <label class="sq-choice" for={control.id}
      >{@render children(control)}<span>{@render caption()}</span></label
    >
  {:else}
    <label for={control.id}>{@render caption()}</label>
    {@render children(control)}
  {/if}
  {#if hint}<p id={`${control.id}-hint`} class="sq-muted">{hint}</p>{/if}
  {#if error}<p id={`${control.id}-error`} role="alert" class="sq-error">{error}</p>{/if}
</div>
