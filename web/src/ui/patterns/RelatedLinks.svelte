<script lang="ts">
  import { cn } from "@/ui/cn";
  import Icon from "@/ui/content/Icon.svelte";
  import type { RelatedLink } from "./related-links";

  /**
   * Counts of a page's related records, such as a build's versions or a creator's records
   * held, as a strip of links under the `PageHeader`. A screen shows the same items in the same
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

<!--
  One bordered strip whose dividers are 1px gaps showing the border color, so they stay right
  however the items wrap. Narrow, it is a full-width list; wide, a row. Never a mix of both.
-->
<nav aria-label={label} class={cn("@container min-w-0 print:hidden", className)}>
  <ul
    class="flex w-full flex-col gap-px overflow-hidden rounded-sm border border-line-strong bg-line @2xl:w-fit @2xl:max-w-full @2xl:flex-row @2xl:flex-wrap"
  >
    {#each items as item (item.href)}
      <li class="flex grow bg-surface">
        <a
          href={item.href}
          class="group flex min-h-control-sm grow items-start gap-2 px-3 py-2 text-sm font-semibold text-fg no-underline transition-colors duration-fast ease-standard hover:bg-surface-muted focus-visible:-outline-offset-3"
        >
          {#if item.icon}
            <!-- Top-aligned on the first line; the padding makes one line exactly the control height. -->
            <span class="flex h-lh items-center text-fg-muted group-hover:text-fg">
              <Icon icon={item.icon} />
            </span>
          {/if}
          <span class="min-w-0 wrap-break-word">{item.label}</span>
          <!-- The whitespace before the count keeps "versions 3" apart in the link's name. -->
          <span
            class="ms-auto shrink-0 rounded-xs bg-surface-muted px-1.5 tabular-nums group-hover:bg-surface"
            >{item.count}</span
          >
        </a>
      </li>
    {/each}
  </ul>
</nav>
