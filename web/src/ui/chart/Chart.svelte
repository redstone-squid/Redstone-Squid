<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "@/ui/cn";
  import Disclosure from "@/ui/data/Disclosure.svelte";
  import Skeleton from "@/ui/feedback/Skeleton.svelte";
  import { toneBackground } from "./styles";
  import type { ChartStatus, ChartTable, ChartTone } from "./types";

  /**
   * The frame every chart uses: a figure captioned by `title` and `description`, an optional
   * legend, the plot, and `table` under a "Show data" disclosure as the accessible version.
   *
   * Hydrated SVG plots reserve their height until their width is measured. Static plots
   * scale the server-rendered SVG to fit. `group` exposes the plot's content, such as
   * links or text, instead of presenting it as one image. While `status` is not `ready`, `statusText` replaces
   * the plot and the table.
   */
  type Props = {
    title: string;
    description: string;
    table?: ChartTable;
    legend?: { label: string; tone: ChartTone }[];
    height?: "sm" | "md" | "lg";
    group?: boolean;
    measurePlot?: boolean;
    status?: ChartStatus;
    statusText?: string;
    labels?: { showData: string };
    class?: string;
    plot: Snippet<[{ width: number; height: number }]>;
  };
  let {
    title,
    description,
    table,
    legend = [],
    height = "md",
    group = false,
    measurePlot = false,
    status = "ready",
    statusText,
    labels,
    class: className,
    plot,
  }: Props = $props();

  const id = $props.id();
  const serverWidth = 640;
  const heights = { sm: 160, md: 240, lg: 320 };
  let measured = $state<number>();
  const hasSize = $derived(measured !== undefined && measured > 0);
</script>

<figure class={cn("grid min-w-0 gap-3", className)} aria-labelledby={`${id}-title`}>
  <figcaption class="grid gap-0.5">
    <span id={`${id}-title`} class="font-semibold">{title}</span>
    <span id={`${id}-description`} class="text-sm text-fg-muted">{description}</span>
  </figcaption>
  {#if legend.length > 1}
    <ul class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
      {#each legend as item (item.label)}
        <li class="flex items-center gap-1.5">
          <span class={cn("size-2.5 rounded-xs", toneBackground[item.tone])} aria-hidden="true"
          ></span>
          {item.label}
        </li>
      {/each}
    </ul>
  {/if}
  {#if status === "ready"}
    <!-- eslint-disable svelte/no-inline-styles -- reserve the same height passed to the plot -->
    <!-- Inline-size containment keeps the drawn plot from holding its track at an older, wider
         measurement, so the plot follows the width it is given. -->
    <div
      style:--plot-height={`${heights[height]}px`}
      bind:clientWidth={measured}
      data-awaiting-width={measurePlot && !hasSize ? "" : undefined}
      role={group ? "group" : "img"}
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-description`}
      class={cn(
        "relative min-w-0 contain-inline-size",
        // LayerChart sizes its layers in pixels. Until the plot has a measured width, as on the
        // server, they flow instead so the SVG scales to the frame through its viewBox.
        !hasSize &&
          "[&_.lc-layout-svg]:h-auto! [&_.lc-layout-svg]:w-full! [&_.lc-tooltip-context]:static! [&_.lc-tooltip-context-container]:static! [&_.lc-tooltip-context-container]:size-auto!",
      )}
    >
      <!-- eslint-enable svelte/no-inline-styles -->
      <!-- A hidden container measures 0; keep the server width until it has a size. -->
      {@render plot({
        width: hasSize && measured !== undefined ? measured : serverWidth,
        height: heights[height],
      })}
    </div>
  {:else}
    <div
      class="grid place-items-center rounded-md border border-dashed border-line p-6 text-center text-sm text-fg-muted"
      aria-busy={status === "loading" ? "true" : undefined}
    >
      {#if status === "loading"}<Skeleton shape="block" class="w-full" />{/if}
      <p class={status === "loading" ? "sr-only" : undefined}>{statusText}</p>
    </div>
  {/if}
  {#if table !== undefined && labels !== undefined && status === "ready"}
    <Disclosure summary={labels.showData}>
      <div class="min-w-0 overflow-x-auto rounded-md border border-line">
        <table class="w-full border-collapse text-sm">
          <caption class="sr-only">{table.caption}</caption>
          <thead class="bg-surface-muted text-xs text-fg-muted">
            <tr>
              {#each table.columns as column (column.key)}
                <th
                  scope="col"
                  class={cn(
                    "px-3 py-2 font-semibold whitespace-nowrap",
                    column.numeric === true ? "text-end" : "text-start",
                  )}>{column.label}</th
                >
              {/each}
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            {#each table.rows as row, index (index)}
              <tr>
                {#each table.columns as column, columnIndex (column.key)}
                  {#if columnIndex === 0}
                    <th scope="row" class="px-3 py-2 text-start font-normal">{row[column.key]}</th>
                  {:else}
                    <td class={cn("px-3 py-2", column.numeric === true && "text-end tabular-nums")}
                      >{row[column.key] ?? ""}</td
                    >
                  {/if}
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </Disclosure>
  {/if}
</figure>

<style>
  /* Static Astro components and readers without JavaScript use the server-rendered plot. */
  @media (scripting: enabled) {
    :global(astro-island[client]) [data-awaiting-width] {
      height: var(--plot-height);
      content-visibility: hidden;
    }
  }
</style>
