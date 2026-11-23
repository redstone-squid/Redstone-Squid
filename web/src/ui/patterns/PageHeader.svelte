<script lang="ts">
  import type { Snippet } from "svelte";
  import Heading from "@/ui/content/Heading.svelte";
  import Breadcrumbs from "@/ui/navigation/Breadcrumbs.svelte";

  /**
   * The top of a page: breadcrumbs, the page's only `h1`, and optional `media` (an avatar),
   * `meta` (badges, dates), `actions`, and a `notice` such as a status banner. Scripts that
   * replace the page content move focus to the heading.
   */
  type Props = {
    title: string;
    eyebrow?: string;
    description?: string;
    breadcrumbs?: { label: string; items: { label: string; href?: string }[] };
    size?: "display" | "xl";
    media?: Snippet;
    meta?: Snippet;
    actions?: Snippet;
    notice?: Snippet;
  };
  let {
    title,
    eyebrow,
    description,
    breadcrumbs,
    size = "xl",
    media,
    meta,
    actions,
    notice,
  }: Props = $props();
</script>

<header class="grid min-w-0 gap-4">
  {#if breadcrumbs}<Breadcrumbs label={breadcrumbs.label} items={breadcrumbs.items} />{/if}
  {#if notice}{@render notice()}{/if}
  <div class="flex min-w-0 flex-wrap items-end justify-between gap-4">
    <div class="flex min-w-0 flex-1 items-center gap-4">
      {#if media}<div class="shrink-0">{@render media()}</div>{/if}
      <div class="grid min-w-0 gap-2">
        <Heading level={1} {size} {eyebrow} focusTarget>{title}</Heading>
        {#if description}<p class="max-w-prose text-fg-muted">{description}</p>{/if}
      </div>
    </div>
    {#if actions}<div class="flex flex-wrap items-center gap-3">{@render actions()}</div>{/if}
  </div>
  {#if meta}<div class="flex flex-wrap items-center gap-2">{@render meta()}</div>{/if}
</header>
