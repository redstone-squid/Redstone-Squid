<script lang="ts">
  import ArrowDownUp from "@lucide/svelte/icons/arrow-down-up";
  import type { LucideIcon } from "@lucide/svelte";
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";
  import SegmentedControl from "@/ui/navigation/SegmentedControl.svelte";
  import Menu from "@/ui/overlays/Menu.svelte";

  /**
   * The row above a listing: the result `count` ("312 builds"), a `sort` menu of links, the
   * `view` switch, and `extra` actions such as subscribing. Everything is a link, so it works
   * without JavaScript. Set `live` when an island changes the results in place, so the new
   * count is announced. `DataTable` renders its own column menu.
   */
  type Props = {
    count?: string;
    live?: boolean;
    sort?: {
      /** Names the menu. */
      label: string;
      /** The trigger text, naming the current order, such as "Sort: Newest". */
      current: string;
      options: { label: string; href: string; current?: boolean }[];
    };
    view?: {
      label: string;
      items: { label: string; href: string; current?: boolean; icon?: LucideIcon }[];
    };
    extra?: Snippet;
    class?: string;
  };
  let { count, live = false, sort, view, extra, class: className }: Props = $props();
</script>

<div class={cn("flex min-w-0 flex-wrap items-center gap-x-4 gap-y-3", className)}>
  {#if count !== undefined}
    <p class="me-auto text-sm font-semibold text-fg-muted" role={live ? "status" : undefined}>
      {count}
    </p>
  {/if}
  <div class="ms-auto flex flex-wrap items-center gap-3">
    {@render extra?.()}
    {#if sort}
      <Menu
        nojs
        label={sort.label}
        trigger={{ label: sort.current, icon: ArrowDownUp, size: "sm" }}
        items={sort.options}
      />
    {/if}
    {#if view}<SegmentedControl label={view.label} items={view.items} />{/if}
  </div>
</div>
