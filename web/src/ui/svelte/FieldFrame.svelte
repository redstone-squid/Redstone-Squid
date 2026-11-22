<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";
  import { fieldAttributes, type FieldMessages } from "@/ui/field";
  import { fieldClass, mutedClass } from "@/ui/variants";
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
    <small class="font-normal text-fg-muted">({detail})</small>{/if}{/snippet}
<div class={fieldClass}>
  {#if inline}
    <label class="flex items-baseline gap-2" for={control.id}
      >{@render children(control)}<span>{@render caption()}</span></label
    >
  {:else}
    <label class="font-semibold" for={control.id}>{@render caption()}</label>
    {@render children(control)}
  {/if}
  {#if hint}<p id={`${control.id}-hint`} class={cn(mutedClass, "text-[0.8125rem]")}>{hint}</p>{/if}
  {#if error}<p id={`${control.id}-error`} role="alert" class="text-[0.8125rem] text-danger">
      {error}
    </p>{/if}
</div>
