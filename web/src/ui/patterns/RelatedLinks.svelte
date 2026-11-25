<script lang="ts">
  import { cn } from "@/ui/cn";
  import Icon from "@/ui/content/Icon.svelte";
  import type { RelatedLink } from "./related-links";

  /**
   * Counts of a page's related records, such as a build's versions or a creator's records
   * held, as a row of links under the `PageHeader`. A screen shows the same items in the same
   * order whatever the data, so a zero count still renders; leave an item out only on pages
   * where it can never apply. Verbs belong in the `PageHeader` actions instead.
   */
  type Props = {
    items: RelatedLink[];
    /** The navigation landmark's name, distinct from other navigation on the page. */
    label: string;
    class?: string;
  };
  let { items, label, class: className }: Props = $props();
</script>

<nav aria-label={label} class={cn("@container min-w-0 print:hidden", className)}>
  <ul class="flex flex-wrap gap-2">
    {#each items as item (item.href)}
      <!-- Narrow, the links share each row evenly; wider, they keep their width up to a cap. -->
      <li class="flex min-w-0 grow basis-32 @lg:max-w-64 @lg:grow-0 @lg:basis-auto">
        <!-- The space between the spans keeps "3 records" apart in the link's name. -->
        <a
          href={item.href}
          class="group grid min-h-control min-w-28 grow content-start gap-0.5 rounded-sm border border-line bg-surface px-4 py-2 text-fg no-underline transition-[background-color,border-color] duration-fast ease-standard hover:border-line-strong hover:bg-surface-muted"
        >
          <span class="text-xl leading-tight font-bold tracking-tight tabular-nums"
            >{item.count}</span
          >
          <span class="flex min-w-0 items-center gap-1.5 text-sm text-fg-muted group-hover:text-fg">
            {#if item.icon}<Icon icon={item.icon} />{/if}
            <span class="min-w-0 wrap-break-word">{item.label}</span>
          </span>
        </a>
      </li>
    {/each}
  </ul>
</nav>
