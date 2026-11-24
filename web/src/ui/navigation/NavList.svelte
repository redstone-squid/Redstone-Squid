<script lang="ts">
  import type { LucideIcon } from "@lucide/svelte";
  import { cn } from "@/ui/cn";

  type NavItem = {
    label: string;
    href: string;
    current?: boolean;
    count?: string;
    icon?: LucideIcon;
  };
  type NavGroup = { heading?: string; items: NavItem[] };

  /**
   * Vertical navigation between sections, such as account settings or a table of contents.
   * `count` is a formatted badge, such as unread notifications.
   */
  type Props = { groups: NavGroup[]; label: string; class?: string };
  let { groups, label, class: className }: Props = $props();
</script>

<nav aria-label={label} class={cn("grid min-w-0 gap-6 text-sm", className)}>
  {#each groups as group, index (index)}
    <div class="grid gap-1">
      {#if group.heading}<p class="px-3 pb-1 text-xs font-semibold text-fg-muted">
          {group.heading}
        </p>{/if}
      <ul class="grid gap-0.5">
        {#each group.items as item (item.href)}
          <li>
            <a
              href={item.href}
              aria-current={item.current === true ? "page" : undefined}
              class="flex min-h-control-sm items-center gap-3 rounded-sm px-3 py-1.5 text-fg no-underline hover:bg-surface-muted aria-[current=page]:forced-highlight aria-[current=page]:bg-accent-soft aria-[current=page]:font-semibold"
            >
              {#if item.icon}<item.icon
                  class="size-4 shrink-0 text-fg-muted"
                  aria-hidden="true"
                />{/if}
              <span class="min-w-0 flex-1 truncate">{item.label}</span>
              {#if item.count !== undefined}<span
                  class="rounded-xs bg-surface-muted px-1.5 text-xs font-semibold text-fg-muted"
                  >{item.count}</span
                >{/if}
            </a>
          </li>
        {/each}
      </ul>
    </div>
  {/each}
</nav>
