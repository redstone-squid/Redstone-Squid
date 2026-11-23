<script lang="ts">
  import { cn } from "@/ui/cn";

  type Tab = { label: string; href: string; current?: boolean; count?: string };

  /**
   * Switches between views of one listing whose state lives in the URL, such as search
   * scopes or draft statuses. Each tab is a link, so it works without JavaScript and the
   * browser's back button returns to the previous view. Scrolls sideways when tabs overflow.
   */
  type Props = { items: Tab[]; label: string; class?: string };
  let { items, label, class: className }: Props = $props();
</script>

<nav aria-label={label} class={cn("min-w-0 border-b border-line", className)}>
  <ul class="-mb-px flex gap-1 overflow-x-auto">
    {#each items as tab (tab.href)}
      <li class="shrink-0">
        <a
          href={tab.href}
          aria-current={tab.current === true ? "page" : undefined}
          class="flex min-h-control items-center gap-2 border-b-2 border-transparent px-3 text-sm font-semibold whitespace-nowrap text-fg-muted no-underline hover:text-fg aria-[current=page]:border-accent aria-[current=page]:text-fg"
        >
          {tab.label}
          {#if tab.count !== undefined}<span
              class="rounded-xs bg-surface-muted px-1.5 text-xs text-fg-muted">{tab.count}</span
            >{/if}
        </a>
      </li>
    {/each}
  </ul>
</nav>
