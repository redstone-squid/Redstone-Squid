<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import { cn } from "@/ui/cn";

  /** Centers content at a readable width with the page gutter on both sides. */
  type Props = HTMLAttributes<HTMLElement> & {
    as?: "div" | "main" | "header" | "footer" | "section";
    width?: "content" | "prose" | "form" | "wide";
    children?: Snippet;
  };
  let { as = "div", width = "content", class: className, children, ...rest }: Props = $props();
  const widths = {
    content: "max-w-content",
    prose: "max-w-prose",
    form: "max-w-form",
    wide: "max-w-wide",
  };
</script>

<svelte:element
  this={as}
  class={cn("mx-auto w-full min-w-0 px-gutter", widths[width], className)}
  {...rest}
>
  {@render children?.()}
</svelte:element>
