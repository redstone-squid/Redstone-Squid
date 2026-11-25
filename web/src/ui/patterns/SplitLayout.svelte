<script lang="ts">
  import PanelRight from "@lucide/svelte/icons/panel-right";
  import X from "@lucide/svelte/icons/x";
  import type { Snippet } from "svelte";
  import Button from "@/ui/actions/Button.svelte";
  import { cn } from "@/ui/cn";
  import { triggerButtonProps, type OverlayTrigger } from "@/ui/overlays/props";

  /**
   * A sidebar beside the main content, such as filters or section navigation. It sits on
   * `side` once the layout is wide enough, measured on the layout itself, not the window.
   *
   * When narrow, `collapse="stack"` puts the sidebar above the content, and `"sheet"` hides it
   * behind a `trigger` button, named `sidebarLabel` by default, that opens it as a panel from the screen edge. The
   * panel is a native popover, so it works without JavaScript, closes on Escape or an outside
   * click, and is the same markup as the wide sidebar: its fields keep their ids and values.
   * Give the sheet a `closeLabel`.
   */
  type Props = {
    sidebar: Snippet;
    children: Snippet;
    /** Names the sidebar landmark and titles the sheet. */
    sidebarLabel: string;
    side?: "start" | "end";
    /** Keeps the wide sidebar in view while the content scrolls. */
    sticky?: boolean;
    collapse?: "stack" | "sheet";
    trigger?: OverlayTrigger;
    closeLabel?: string;
    class?: string;
  };
  let {
    sidebar,
    children,
    sidebarLabel,
    side = "start",
    sticky = false,
    collapse = "stack",
    trigger,
    closeLabel = "",
    class: className,
  }: Props = $props();
  const id = $props.id();
</script>

<div class={cn("@container min-w-0", className)}>
  <div
    class={cn(
      "grid min-w-0 gap-6 @3xl:gap-8",
      side === "start"
        ? "@3xl:grid-cols-[var(--container-sidebar)_minmax(0,1fr)]"
        : "@3xl:grid-cols-[minmax(0,1fr)_var(--container-sidebar)]",
    )}
  >
    <aside
      aria-label={sidebarLabel}
      class={cn(
        "min-w-0",
        side === "end" && "@3xl:col-start-2 @3xl:row-start-1",
        sticky &&
          "@3xl:sticky @3xl:top-4 @3xl:max-h-[calc(100dvh-2rem)] @3xl:self-start @3xl:overflow-y-auto @3xl:overscroll-contain",
      )}
    >
      {#if collapse === "sheet"}
        {@const button = trigger ?? { label: sidebarLabel }}
        <Button
          icon={PanelRight}
          {...triggerButtonProps(button)}
          class="@3xl:hidden"
          popovertarget={`${id}-sheet`}
          aria-controls={`${id}-sheet`}>{button.label}</Button
        >
        <!-- Follows its button directly, so browsers add no implicit aria-details. -->
        <div
          id={`${id}-sheet`}
          popover="auto"
          class={cn(
            "fixed inset-y-0 m-0 h-dvh max-h-none w-[calc(100%-3rem)] max-w-dialog-sm overflow-y-auto overscroll-contain",
            "border-line bg-surface-raised p-panel text-fg shadow-overlay backdrop:bg-overlay",
            "motion-safe:transition-[translate] motion-safe:duration-normal motion-safe:ease-standard",
            side === "start"
              ? "start-0 end-auto rounded-e-lg border-e motion-safe:starting:-translate-x-full rtl:motion-safe:starting:translate-x-full"
              : "start-auto end-0 rounded-s-lg border-s motion-safe:starting:translate-x-full rtl:motion-safe:starting:-translate-x-full",
            "@3xl:popover-inline @3xl:block",
          )}
        >
          <div class="mb-4 flex items-center justify-between gap-4 @3xl:hidden">
            <p class="text-xl font-bold tracking-tight">{sidebarLabel}</p>
            <Button
              variant="quiet"
              iconOnly
              icon={X}
              label={closeLabel}
              popovertarget={`${id}-sheet`}
              popovertargetaction="hide"
            />
          </div>
          {@render sidebar()}
        </div>
      {:else}
        {@render sidebar()}
      {/if}
    </aside>
    <div class="min-w-0">{@render children()}</div>
  </div>
</div>
