<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";
  import Heading from "@/ui/content/Heading.svelte";

  /**
   * A titled part of a page, labelled by its heading. `surface` draws it as a panel; `actions`
   * sit beside the title, such as "View all".
   */
  type Props = {
    title: string;
    level?: 2 | 3;
    description?: string;
    surface?: boolean;
    id?: string;
    actions?: Snippet;
    class?: string;
    children: Snippet;
  };
  let {
    title,
    level = 2,
    description,
    surface = false,
    id,
    actions,
    class: className,
    children,
  }: Props = $props();
  const generated = $props.id();
  const headingId = $derived(id ?? `${generated}-heading`);
</script>

<section
  aria-labelledby={headingId}
  class={cn(
    "grid min-w-0 content-start gap-4",
    surface && "rounded-md border border-line bg-surface p-4 shadow-card md:p-6",
    className,
  )}
>
  <div class="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
    <div class="grid min-w-0 gap-1">
      <Heading {level} id={headingId}>{title}</Heading>
      {#if description}<p class="max-w-prose text-sm text-fg-muted">{description}</p>{/if}
    </div>
    {#if actions}<div class="flex flex-wrap items-center gap-3">{@render actions()}</div>{/if}
  </div>
  {@render children()}
</section>
