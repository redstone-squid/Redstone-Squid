<script lang="ts">
  import type { HTMLButtonAttributes } from "svelte/elements";
  import type { Snippet } from "svelte";
  import { buttonStyles, type ButtonVariants } from "@/ui/variants";
  let {
    variant,
    size,
    class: className,
    type = "button",
    children,
    ...props
  }: HTMLButtonAttributes & ButtonVariants & { children?: Snippet } = $props();
</script>

<button {type} class={buttonStyles({ variant, size, className })} {...props}>
  {#if children}{@render children()}{/if}
</button>
