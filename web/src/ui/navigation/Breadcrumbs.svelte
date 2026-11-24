<script lang="ts">
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import { cn } from "@/ui/cn";

  type Crumb = { label: string; href?: string };

  /**
   * Where the page sits, from the outermost section to the current page, which is last and
   * has no link. Below 32rem wide, only the parent and the current page are shown.
   */
  type Props = { items: Crumb[]; label: string; class?: string };
  let { items, label, class: className }: Props = $props();
</script>

<nav aria-label={label} class={cn("@container min-w-0 text-sm", className)}>
  <ol class="flex min-w-0 flex-wrap items-center gap-1 text-fg-muted">
    {#each items as item, index (index)}
      {@const last = index === items.length - 1}
      <li
        class={cn("flex min-w-0 items-center gap-1", index < items.length - 2 && "@max-lg:hidden")}
      >
        {#if item.href !== undefined && !last}
          <a
            class="truncate rounded-xs text-fg-muted no-underline hover:text-fg hover:underline"
            href={item.href}>{item.label}</a
          >
        {:else}
          <span class={cn("truncate", last && "text-fg")} aria-current={last ? "page" : undefined}
            >{item.label}</span
          >
        {/if}
        {#if !last}<ChevronRight class="size-3.5 shrink-0" aria-hidden="true" />{/if}
      </li>
    {/each}
  </ol>
</nav>
