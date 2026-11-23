<script lang="ts">
  import type { LucideIcon } from "@lucide/svelte";
  import { cn } from "@/ui/cn";

  type Segment = { label: string; href: string; current?: boolean; icon?: LucideIcon };

  /**
   * A compact choice between a few views kept in the URL, such as cards or table. Each segment
   * is a link. For a choice inside a form, use `ChoiceGroup`.
   */
  type Props = { items: Segment[]; label: string; size?: "sm" | "md"; class?: string };
  let { items, label, size = "sm", class: className }: Props = $props();
</script>

<nav aria-label={label} class={cn("w-fit max-w-full", className)}>
  <ul class="flex rounded-sm border border-line-strong bg-surface p-0.5">
    {#each items as item (item.href)}
      <li>
        <a
          href={item.href}
          aria-current={item.current === true ? "true" : undefined}
          class={cn(
            "flex items-center gap-2 rounded-xs px-3 font-semibold whitespace-nowrap text-fg-muted no-underline hover:text-fg aria-[current=true]:bg-accent-soft aria-[current=true]:text-fg",
            size === "sm" ? "min-h-control-sm text-sm" : "min-h-control text-sm",
          )}
          >{#if item.icon}<item.icon class="size-4" aria-hidden="true" />{/if}{item.label}</a
        >
      </li>
    {/each}
  </ul>
</nav>
