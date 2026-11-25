<script lang="ts">
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";

  type Page = { label: string; href: string; current?: boolean; name?: string };

  /**
   * Links between pages of a listing. Previous and next keep their places at either end and
   * are shown disabled when there is no page in that direction; the control disappears when
   * there is only one page. `range` is the formatted position, such as "21–40 of 312".
   * `pages` adds numbered links for listings addressed by offset, hidden below 32rem wide;
   * `name` is each link's full accessible name, such as "Page 3".
   */
  type Props = {
    prevHref?: string | null;
    nextHref?: string | null;
    pages?: Page[];
    range?: string;
    labels: { nav: string; previous: string; next: string };
    class?: string;
  };
  let {
    prevHref = null,
    nextHref = null,
    pages = [],
    range,
    labels,
    class: className,
  }: Props = $props();
  const visible = $derived(prevHref !== null || nextHref !== null || pages.length > 1);
</script>

{#if visible}
  <nav aria-label={labels.nav} class={cn("@container grid min-w-0 gap-3 print:hidden", className)}>
    <div class="flex flex-wrap items-center justify-between gap-3">
      <Button
        size="sm"
        href={prevHref ?? ""}
        disabled={prevHref === null}
        rel="prev"
        icon={ChevronLeft}>{labels.previous}</Button
      >
      {#if pages.length > 1}
        <ol class="flex flex-wrap items-center gap-1 @max-lg:hidden">
          {#each pages as page (page.href)}
            <li>
              <Button
                size="sm"
                variant={page.current === true ? "primary" : "quiet"}
                href={page.href}
                class="current:forced-highlight"
                aria-label={page.name}
                aria-current={page.current === true ? "page" : undefined}>{page.label}</Button
              >
            </li>
          {/each}
        </ol>
      {:else if range !== undefined}
        <span class="text-sm text-fg-muted">{range}</span>
      {/if}
      <Button
        size="sm"
        href={nextHref ?? ""}
        disabled={nextHref === null}
        rel="next"
        iconEnd={ChevronRight}>{labels.next}</Button
      >
    </div>
    {#if pages.length > 1 && range !== undefined}
      <p class="text-center text-sm text-fg-muted">{range}</p>
    {/if}
  </nav>
{/if}
