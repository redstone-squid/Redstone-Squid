<script lang="ts">
  import { Tabs as Primitive } from "bits-ui";
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";

  type LinkTab = { label: string; href: string; current?: boolean; count?: string };
  type PanelTab = { id: string; label: string; count?: string; disabled?: boolean };

  /**
   * Switches between views. Scrolls sideways when tabs overflow.
   *
   * In `link` mode (the default) each tab is a link, for listings whose view lives in the URL,
   * such as search scopes; it works without JavaScript and Back returns to the previous view.
   *
   * `panel` mode switches in-page content that needs no URL, rendering `panel(tab)` for the
   * selected tab. Arrow keys move between tabs and select them. Without JavaScript only the
   * first panel shows, so use it for alternatives, not for content found nowhere else.
   */
  type Props = { label: string; class?: string } & (
    | { mode?: "link"; items: LinkTab[] }
    | { mode: "panel"; items: PanelTab[]; value?: string; panel: Snippet<[PanelTab]> }
  );
  let {
    label,
    class: className,
    value = $bindable(),
    ...props
  }: Props & { value?: string } = $props();

  const tabClass = cn(
    "flex min-h-control shrink-0 cursor-pointer items-center gap-2 border-b-2 border-transparent px-3 text-sm font-semibold whitespace-nowrap text-fg-muted no-underline hover:text-fg",
    "aria-[current=page]:border-accent aria-[current=page]:forced-highlight aria-[current=page]:text-fg",
    "aria-selected:border-accent aria-selected:forced-highlight aria-selected:text-fg",
    "disabled:cursor-not-allowed disabled:opacity-60",
  );
</script>

{#snippet tabLabel(tab: { label: string; count?: string })}
  {tab.label}
  {#if tab.count !== undefined}<span
      class="rounded-xs bg-surface-muted px-1.5 text-xs text-fg-muted">{tab.count}</span
    >{/if}
{/snippet}

{#if props.mode === "panel"}
  {@const { items, panel } = props}
  <Primitive.Root
    bind:value={() => value ?? items[0]?.id ?? "", (next) => (value = next)}
    class={cn("grid min-w-0 gap-4", className)}
  >
    <div class="min-w-0 border-b border-line">
      <Primitive.List aria-label={label} class="-mb-px flex gap-1 overflow-x-auto">
        {#each items as tab (tab.id)}
          <Primitive.Trigger value={tab.id} disabled={tab.disabled} class={tabClass}
            >{@render tabLabel(tab)}</Primitive.Trigger
          >
        {/each}
      </Primitive.List>
    </div>
    {#each items as tab (tab.id)}
      <Primitive.Content value={tab.id} class="min-w-0">{@render panel(tab)}</Primitive.Content>
    {/each}
  </Primitive.Root>
{:else}
  <nav aria-label={label} class={cn("min-w-0 border-b border-line", className)}>
    <ul class="-mb-px flex gap-1 overflow-x-auto">
      {#each props.items as tab (tab.href)}
        <li class="shrink-0">
          <a
            href={tab.href}
            aria-current={tab.current === true ? "page" : undefined}
            class={tabClass}>{@render tabLabel(tab)}</a
          >
        </li>
      {/each}
    </ul>
  </nav>
{/if}
