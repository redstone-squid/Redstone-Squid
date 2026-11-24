<script lang="ts">
  import { scaleLinear } from "d3-scale";
  import { Axis, ChartCore, Grid, Svg } from "layerchart";
  import { IsMounted } from "runed";
  import { cn } from "@/ui/cn";
  import Chart from "./Chart.svelte";
  import { axisClasses, toneFill } from "./styles";
  import type { ChartAxis, ChartStatus, ChartTable, RangeSegment } from "./types";

  /**
   * Values over runs of whole numbers, such as the smallest record door at each width. Each
   * segment covers `from` to `to` inclusive at height `value`; with `step`, only the counted
   * widths are drawn solid. An unbounded segment runs off the end of the axis, marked with
   * `unboundedLabel`. Segments with `href` are links.
   *
   * `selected` marks one width. With `onselect`, the hydrated plot also takes focus: arrow
   * keys move the selection by one, Home and End jump to either end, and a click picks the
   * nearest width.
   */
  type Props = {
    title: string;
    description: string;
    segments: RangeSegment[];
    x: ChartAxis<number>;
    y: ChartAxis<number>;
    unboundedLabel: string;
    selected?: number;
    onselect?: (width: number) => void;
    table?: ChartTable;
    height?: "sm" | "md" | "lg";
    status?: ChartStatus;
    statusText?: string;
    labels: { showData: string; selected: string };
    class?: string;
  };
  let {
    title,
    description,
    segments,
    x,
    y,
    unboundedLabel,
    selected,
    onselect,
    table,
    height,
    status,
    statusText,
    labels,
    class: className,
  }: Props = $props();

  const formatX = (value: number) => x.format?.(value) ?? String(value);
  const formatY = (value: number) => y.format?.(value) ?? String(value);
  const counts = (segment: RangeSegment, width: number) =>
    segment.step === undefined || (width - (segment.residue ?? 0)) % segment.step === 0;

  const start = $derived(x.domain?.[0] ?? Math.min(...segments.map((segment) => segment.from)));
  const lastFinite = $derived(Math.max(...segments.map((segment) => segment.to ?? segment.from)));
  // An unbounded segment gets a fifth of the axis beyond the last bounded width.
  const end = $derived(
    x.domain?.[1] ??
      (segments.some((segment) => segment.to === null)
        ? Math.ceil(lastFinite + (lastFinite - start) / 5 + 1)
        : lastFinite),
  );
  const yMax = $derived(Math.max(1, ...segments.map((segment) => segment.value)));

  const derivedTable = $derived<ChartTable>(
    table ?? {
      caption: title,
      columns: [
        { key: "range", label: x.label },
        { key: "label", label: title },
        { key: "value", label: y.label, numeric: true },
      ],
      rows: segments.map((segment) => ({
        range:
          segment.to === null
            ? `${formatX(segment.from)} ${unboundedLabel}`
            : segment.to === segment.from
              ? formatX(segment.from)
              : `${formatX(segment.from)}–${formatX(segment.to)}`,
        label: segment.label,
        value: formatY(segment.value),
      })),
    },
  );

  // Pointer and keyboard handling only exist once hydrated.
  const mounted = new IsMounted();

  function onkeydown(event: KeyboardEvent) {
    const current = selected ?? start;
    const moves: Record<string, number> = {
      ArrowRight: Math.min(end, current + 1),
      ArrowLeft: Math.max(start, current - 1),
      Home: start,
      End: end,
    };
    const next = moves[event.key];
    if (next !== undefined) {
      event.preventDefault();
      onselect?.(next);
    }
  }
  const selectedSegment = $derived(
    selected === undefined
      ? undefined
      : segments.find(
          (segment) =>
            selected >= segment.from &&
            selected <= (segment.to ?? end) &&
            counts(segment, selected),
        ),
  );
</script>

<Chart
  {title}
  {description}
  table={derivedTable}
  {height}
  group={onselect !== undefined || segments.some((segment) => segment.href !== undefined)}
  {status}
  {statusText}
  {labels}
  class={className}
>
  {#snippet plot({ width, height: plotHeight })}
    {#if onselect !== undefined && mounted.current}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
      <div
        tabindex="0"
        role="application"
        aria-label={title}
        class="pointer-events-none absolute inset-0 z-sticky rounded-sm"
        {onkeydown}
      ></div>
      <p class="sr-only" role="status">
        {#if selected !== undefined}{labels.selected}
          {formatX(selected)}{#if selectedSegment !== undefined}: {selectedSegment.label}, {formatY(
              selectedSegment.value,
            )}{/if}{/if}
      </p>
    {/if}
    <ChartCore
      ssr
      {width}
      height={plotHeight}
      data={segments}
      xScale={scaleLinear()}
      xDomain={[start - 0.5, end + 0.5]}
      yDomain={y.domain ?? [0, yMax]}
      yNice
      padding={{ top: 28, right: 12, bottom: 28, left: 44 }}
      tooltipContext={false}
      class="w-full!"
    >
      {#snippet children({ context })}
        {@const px = (value: number) => context.xScale(value) as number}
        {@const py = (value: number) => context.yScale(value) as number}
        {@const unit = px(1) - px(0)}
        <Svg viewBox={`0 0 ${width} ${plotHeight}`} class="size-full overflow-visible">
          <Grid y classes={{ line: "stroke-line" }} />
          <Axis
            placement="left"
            ticks={Math.max(2, Math.round(plotHeight / 60))}
            format={formatY}
            label={y.label}
            labelPlacement="start"
            classes={axisClasses}
          />
          <Axis
            placement="bottom"
            ticks={(scale) =>
              (scale.ticks?.(Math.max(2, Math.floor(width / 72))) ?? []).filter((tick: number) =>
                Number.isInteger(tick),
              )}
            format={formatX}
            rule
            classes={axisClasses}
          />
          {#each segments as segment (segment.from)}
            {@const to = segment.to ?? end}
            {@const top = py(segment.value)}
            {@const name = `${derivedTable.rows[segments.indexOf(segment)]?.range ?? ""}: ${segment.label}, ${formatY(segment.value)}`}
            {@const fill = toneFill[segment.tone ?? 1]}
            {#snippet marks()}
              <title>{name}</title>
              {#if segment.step === undefined || unit < 4}
                <rect
                  x={px(segment.from - 0.5) + 1}
                  y={top}
                  width={Math.max(1, px(to + 0.5) - px(segment.from - 0.5) - 2)}
                  height={context.height - top}
                  rx={2}
                  class={cn(fill, segment.step !== undefined && "opacity-60")}
                />
              {:else}
                <!-- Uncounted widths stay faint so the gaps read as gaps, not missing data. -->
                <rect
                  x={px(segment.from - 0.5) + 1}
                  y={top}
                  width={Math.max(1, px(to + 0.5) - px(segment.from - 0.5) - 2)}
                  height={context.height - top}
                  rx={2}
                  class={cn(fill, "opacity-20")}
                />
                {#each { length: to - segment.from + 1 }, offset (offset)}
                  {#if counts(segment, segment.from + offset)}
                    <rect
                      x={px(segment.from + offset - 0.3)}
                      y={top}
                      width={unit * 0.6}
                      height={context.height - top}
                      class={fill}
                    />
                  {/if}
                {/each}
              {/if}
            {/snippet}
            {#if segment.href !== undefined}
              <a href={segment.href} aria-label={name} class="group/mark no-underline">
                {@render marks()}
              </a>
            {:else}
              <g>{@render marks()}</g>
            {/if}
            {#if segment.to === null}
              <text x={context.width} y={top - 6} text-anchor="end" class="fill-fg-muted text-xs"
                >{unboundedLabel}</text
              >
            {/if}
          {/each}
          {#if selected !== undefined}
            <g class="text-xs">
              <line
                x1={px(selected)}
                x2={px(selected)}
                y1={-4}
                y2={context.height}
                class="stroke-fg"
                stroke-width="2"
              />
              <text
                x={px(selected)}
                y={-2}
                text-anchor={px(selected) > context.width * 0.8 ? "end" : "start"}
                dx={px(selected) > context.width * 0.8 ? -6 : 6}
                class="fill-fg font-semibold">{formatX(selected)}</text
              >
            </g>
          {/if}
          {#if onselect !== undefined && mounted.current}
            <rect
              role="presentation"
              x={0}
              y={0}
              width={context.width}
              height={context.height}
              class="fill-transparent"
              onclick={(event) => {
                const scale = width / (event.currentTarget.ownerSVGElement?.clientWidth ?? width);
                const value = Number(
                  context.xScale.invert?.(event.offsetX * scale - context.padding.left),
                );
                onselect(Math.min(end, Math.max(start, Math.round(value))));
              }}
            />
          {/if}
        </Svg>
      {/snippet}
    </ChartCore>
  {/snippet}
</Chart>
