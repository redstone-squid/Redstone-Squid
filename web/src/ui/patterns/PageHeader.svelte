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

<!--
  Everything after the media shares one column, so meta lines up with the title rather than the
  avatar. Actions come last, which is where a narrow header shows them; a wide one places them
  beside the title's last line.
-->
<header class="@container grid min-w-0 gap-4">
  {#if breadcrumbs}<Breadcrumbs label={breadcrumbs.label} items={breadcrumbs.items} />{/if}
  {#if notice}{@render notice()}{/if}
  <div class="flex min-w-0 items-start gap-4">
    {#if media}<div class="shrink-0">{@render media()}</div>{/if}
    <div
      class="grid min-w-0 flex-1 grid-cols-1 gap-2 @xl:grid-cols-[minmax(0,1fr)_auto] @xl:gap-x-4"
    >
      <div class="min-w-0"><Heading level={1} {size} {eyebrow} focusTarget>{title}</Heading></div>
      {#if description}<p class="max-w-prose wrap-break-word text-fg-muted @xl:col-start-1">
          {description}
        </p>{/if}
      {#if meta}<div class="flex flex-wrap items-center gap-2 @xl:col-start-1">
          {@render meta()}
        </div>{/if}
      {#if actions}<div
          class="flex flex-wrap items-center gap-3 pt-2 @xl:col-start-2 @xl:row-start-1 @xl:self-end @xl:pt-0"
        >
          {@render actions()}
        </div>{/if}
    </div>
  </div>
</header>
