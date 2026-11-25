<script lang="ts">
  import MenuIcon from "@lucide/svelte/icons/menu";
  import type { Snippet } from "svelte";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import { floatingPanelClass } from "@/ui/overlays/styles";

  /**
   * The body of a site header: the `home` link with the site's logo, the primary `links`, and `actions` such as
   * the language menu and the user menu. `current` marks the page's section.
   *
   * When the bar is too narrow for the links (measured on the bar, not the window), they
   * collapse behind a button named `menuLabel`. The list is a native popover in that layout
   * and plain inline links otherwise, so it is one navigation landmark that works without
   * JavaScript; links stay in the Tab order, with no menu roles or arrow keys.
   */
  type Props = {
    home: { label: string; href: string; logo?: string };
    links: { label: string; href: string; current?: boolean }[];
    actions?: Snippet;
    /** Names the navigation landmark. */
    label: string;
    menuLabel: string;
    class?: string;
  };
  let { home, links, actions, label, menuLabel, class: className }: Props = $props();
  const id = $props.id();
</script>

<div class={cn("@container min-w-0", className)}>
  <div class="flex min-h-header flex-wrap items-center gap-x-6 gap-y-3 py-3">
    <a
      href={home.href}
      class="inline-flex shrink-0 items-center gap-3 rounded-sm font-bold whitespace-nowrap no-underline"
    >
      {#if home.logo !== undefined}<img
          class="size-8"
          src={home.logo}
          width="32"
          height="32"
          alt=""
        />{/if}{home.label}
    </a>
    <nav aria-label={label} class="min-w-0 @4xl:flex-1 print:hidden">
      <Button
        class="@4xl:hidden"
        icon={MenuIcon}
        popovertarget={`${id}-links`}
        aria-controls={`${id}-links`}>{menuLabel}</Button
      >
      <!-- Follows its button directly, so browsers add no implicit aria-details. -->
      <ul
        id={`${id}-links`}
        popover="auto"
        data-align="start"
        class={cn(
          floatingPanelClass,
          "dropdown-start max-h-[70dvh] min-w-56 overflow-auto p-1",
          "@4xl:popover-inline @4xl:flex @4xl:items-center @4xl:gap-6",
        )}
      >
        {#each links as link (link.href)}
          <li>
            <a
              href={link.href}
              aria-current={link.current === true ? "page" : undefined}
              class={cn(
                "flex min-h-control-sm items-center rounded-xs px-3 py-2 text-base text-fg-muted no-underline hover:bg-surface-muted hover:text-fg",
                "current:forced-highlight current:font-semibold current:text-fg",
                "border-s-2 border-transparent current:border-accent",
                "@4xl:min-h-0 @4xl:rounded-none @4xl:border-s-0 @4xl:border-b-2 @4xl:px-0 @4xl:hover:bg-transparent",
              )}>{link.label}</a
            >
          </li>
        {/each}
      </ul>
    </nav>
    {#if actions}
      <div class="ms-auto flex flex-wrap items-center gap-2 print:hidden">{@render actions()}</div>
    {/if}
  </div>
</div>
