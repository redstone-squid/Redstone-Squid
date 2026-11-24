<script lang="ts">
  import { scaleBand, scaleLinear, scaleTime } from "d3-scale";
  import { Axis, ChartCore, Grid, Svg } from "layerchart";
  import { IsMounted } from "runed";
  import { SvelteMap } from "svelte/reactivity";
  import { cn } from "@/ui/cn";
  import Chart from "./Chart.svelte";
  import { axisClasses, toneFill, toneStroke } from "./styles";
  import type { ChartAxis, ChartStatus, ChartTable, Series, SeriesPoint } from "./types";

  type X = SeriesPoint["x"];

  /**
   * Lines, steps or bars over a shared x axis, with one y axis from zero. `x.type` picks the
   * scale: `band` for categories such as years, `time` for dates, `linear` for numbers. A
   * point with `href` is a link, so marks work without JavaScript. `annotations` draw
   * labelled threshold lines across the plot.
   *
   * With `interactive`, the hydrated plot shows a tooltip under the pointer and takes focus:
   * arrow keys move between x values, Home and End jump to either end, the tooltip text is
   * announced, and Enter follows the focused point's link.
   */
  type Props = {
    title: string;
    description: string;
    kind: "line" | "step" | "bar" | "stacked-bar";
    x: ChartAxis<X> & { type: "linear" | "time" | "band" };
    y: ChartAxis<number>;
    series: Series[];
    annotations?: { value: number; label: string }[];
    table?: ChartTable;
    height?: "sm" | "md" | "lg";
    interactive?: boolean;
    status?: ChartStatus;
    statusText?: string;
    labels: { showData: string };
    class?: string;
  };
  let {
    title,
    description,
    kind,
    x,
    y,
    series,
    annotations = [],
    table,
    height,
    interactive = false,
    status,
    statusText,
    labels,
    class: className,
  }: Props = $props();

  const formatX = (value: X) =>
    x.format?.(value) ?? (value instanceof Date ? value.toISOString().slice(0, 10) : String(value));
  const formatY = (value: number) => y.format?.(value) ?? String(value);
  const key = (value: X) => (value instanceof Date ? value.getTime() : value);

  /** Every x value once, in order, with each series' point there. */
  const columns = $derived.by(() => {
    const byKey = new SvelteMap<string | number, { x: X; points: (SeriesPoint | undefined)[] }>();
    series.forEach((line, index) => {
      for (const point of line.points) {
        const entry = byKey.get(key(point.x)) ?? { x: point.x, points: [] };
        entry.points[index] = point;
        byKey.set(key(point.x), entry);
      }
    });
    const entries = [...byKey.values()];
    if (x.type !== "band") entries.sort((a, b) => Number(key(a.x)) - Number(key(b.x)));
    return entries;
  });
  const stacked = $derived(kind === "stacked-bar");
  const wholeX = $derived(
    x.type === "linear" && columns.every((column) => Number.isInteger(column.x)),
  );
  const yMax = $derived(
    Math.max(
      ...columns.map((column) =>
        stacked
          ? column.points.reduce((sum, point) => sum + (point?.y ?? 0), 0)
          : Math.max(0, ...column.points.map((point) => point?.y ?? 0)),
      ),
      ...annotations.map((annotation) => annotation.value),
      1,
    ),
  );
  const xScale = $derived(
    x.type === "band"
      ? scaleBand<X>().padding(0.3)
      : x.type === "time"
        ? scaleTime()
        : scaleLinear(),
  );
  const xDomain = $derived(
    x.type === "band"
      ? columns.map((column) => column.x)
      : (x.domain ?? [columns[0]?.x ?? 0, columns.at(-1)?.x ?? 1]),
  );

  const derivedTable = $derived<ChartTable>(
    table ?? {
      caption: title,
      columns: [
        { key: "x", label: x.label },
        ...series.map((line) => ({ key: line.id, label: line.label, numeric: true })),
      ],
      rows: columns.map((column) => ({
        x: formatX(column.x),
        ...Object.fromEntries(
          series.flatMap((line, index) => {
            const point = column.points[index];
            return point === undefined ? [] : [[line.id, formatY(point.y)]];
          }),
        ),
      })),
    },
  );

  let active = $state<number>();
  // Pointer and keyboard handling only exist once hydrated.
  const mounted = new IsMounted();
  const tooltip = $derived.by(() => {
    if (active === undefined) return undefined;
    const column = columns[active];
    if (column === undefined) return undefined;
    return {
      x: column.x,
      heading: formatX(column.x),
      rows: series.flatMap((line, index) => {
        const point = column.points[index];
        return point === undefined ? [] : [{ line, value: formatY(point.y) }];
      }),
      href: column.points.find((point) => point?.href !== undefined)?.href,
    };
  });

  function onkeydown(event: KeyboardEvent) {
    const last = columns.length - 1;
    const moves: Record<string, number> = {
      ArrowRight: Math.min(last, (active ?? -1) + 1),
      ArrowLeft: Math.max(0, (active ?? 1) - 1),
      Home: 0,
      End: last,
    };
    if (event.key in moves) {
      event.preventDefault();
      active = moves[event.key];
    } else if (event.key === "Enter" && tooltip?.href !== undefined) {
      location.assign(tooltip.href);
    } else if (event.key === "Escape") {
      active = undefined;
    }
  }
</script>

<Chart
  {title}
  {description}
  table={derivedTable}
  legend={series.map((line) => ({ label: line.label, tone: line.tone }))}
  {height}
  group={interactive ||
    series.some((line) => line.points.some((point) => point.href !== undefined))}
  {status}
  {statusText}
  {labels}
  class={className}
>
  {#snippet plot({ width, height: plotHeight })}
    {#if interactive && mounted.current}
      <!-- The plot takes focus over the drawing; the live region says what is focused. -->
      <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
      <div
        tabindex="0"
        role="application"
        aria-label={title}
        class="pointer-events-none absolute inset-0 z-sticky rounded-sm"
        {onkeydown}
        onfocus={() => (active ??= 0)}
        onblur={() => (active = undefined)}
      ></div>
      <p class="sr-only" role="status">
        {#if tooltip !== undefined}{tooltip.heading}: {tooltip.rows
            .map((row) => `${row.line.label} ${row.value}`)
            .join(", ")}{/if}
      </p>
    {/if}
    <ChartCore
      ssr
      {width}
      height={plotHeight}
      data={columns}
      x="x"
      {xScale}
      {xDomain}
      yDomain={y.domain ?? [0, yMax]}
      yNice
      padding={{ top: 28, right: 12, bottom: 28, left: 44 }}
      tooltipContext={false}
      class="w-full!"
    >
      {#snippet children({ context })}
        {@const px = (value: X) =>
          (context.xScale(value) as number) +
          (x.type === "band" ? (context.xScale.bandwidth?.() ?? 0) / 2 : 0)}
        {@const py = (value: number) => context.yScale(value) as number}
        {@const bandwidth = context.xScale.bandwidth?.() ?? 0}
        {@const barWidth = Math.min(24, stacked ? bandwidth : bandwidth / series.length)}
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
            ticks={x.type === "band"
              ? undefined
              : (scale) => {
                  const ticks = scale.ticks?.(Math.max(2, Math.floor(width / 120))) ?? [];
                  // Whole-number data, such as months or widths, gets whole-number ticks.
                  return wholeX ? ticks.filter((tick: number) => Number.isInteger(tick)) : ticks;
                }}
            format={formatX}
            rule
            tickOcclusion
            classes={axisClasses}
          />
          {#each annotations as annotation (annotation.label)}
            <g class="text-xs">
              <line
                x1={0}
                x2={context.width}
                y1={py(annotation.value)}
                y2={py(annotation.value)}
                class="stroke-fg-muted"
                stroke-dasharray="4 4"
              />
              <text
                x={context.width}
                y={py(annotation.value) - 6}
                text-anchor="end"
                class="fill-fg-muted">{annotation.label}</text
              >
            </g>
          {/each}
          {#if tooltip !== undefined}
            <line
              x1={px(tooltip.x)}
              x2={px(tooltip.x)}
              y1={0}
              y2={context.height}
              class="stroke-line-strong"
            />
          {/if}
          {#if kind === "line" || kind === "step"}
            {#each series as line (line.id)}
              {@const points = line.points.toSorted((a, b) =>
                x.type === "band" ? 0 : Number(key(a.x)) - Number(key(b.x)),
              )}
              <path
                d={points
                  .map((point, index) => {
                    const previous = points[index - 1];
                    const step =
                      kind === "step" && previous !== undefined
                        ? `L${px(point.x)},${py(previous.y)}`
                        : "";
                    return `${index === 0 ? "M" : `${step}L`}${px(point.x)},${py(point.y)}`;
                  })
                  .join("")}
                class={cn("fill-none", toneStroke[line.tone])}
                stroke-width="2"
                stroke-linejoin="round"
                stroke-linecap="round"
              />
              {#each points as point (key(point.x))}
                {@const isActive =
                  active !== undefined && key(columns[active]?.x ?? "") === key(point.x)}
                {#if point.href !== undefined}
                  <a
                    href={point.href}
                    aria-label={`${line.label}, ${formatX(point.x)}: ${formatY(point.y)}`}
                  >
                    <circle
                      cx={px(point.x)}
                      cy={py(point.y)}
                      r={isActive ? 6 : 4}
                      class={cn("stroke-surface", toneFill[line.tone])}
                      stroke-width="2"
                    />
                  </a>
                {:else if isActive || points.length <= 24}
                  <circle
                    cx={px(point.x)}
                    cy={py(point.y)}
                    r={isActive ? 6 : 4}
                    class={cn("stroke-surface", toneFill[line.tone])}
                    stroke-width="2"
                  />
                {/if}
              {/each}
            {/each}
          {:else}
            {#each columns as column, columnIndex (key(column.x))}
              {@const left = px(column.x) - (stacked ? barWidth : barWidth * series.length) / 2}
              {#each series as line, index (line.id)}
                {@const point = column.points[index]}
                {#if point !== undefined}
                  {@const base = stacked
                    ? column.points.slice(0, index).reduce((sum, below) => sum + (below?.y ?? 0), 0)
                    : 0}
                  {@const top = py(base + point.y)}
                  {@const bottom = py(base)}
                  {@const isTop =
                    !stacked ||
                    column.points.slice(index + 1).every((above) => above === undefined)}
                  {@const rect = {
                    x: stacked ? left : left + index * barWidth,
                    y: top,
                    width: stacked ? barWidth : Math.max(1, barWidth - 2),
                    // A 2px surface gap separates stacked segments.
                    height: Math.max(0, bottom - top - (stacked && index > 0 ? 2 : 0)),
                  }}
                  {@const r = isTop ? Math.min(4, rect.width / 2, rect.height) : 0}
                  {@const d = /* Round the data end; keep the baseline square. */ `M${rect.x},${rect.y + rect.height}V${rect.y + r}Q${rect.x},${rect.y} ${rect.x + r},${rect.y}H${rect.x + rect.width - r}Q${rect.x + rect.width},${rect.y} ${rect.x + rect.width},${rect.y + r}V${rect.y + rect.height}Z`}
                  {#if point.href !== undefined}
                    <a
                      href={point.href}
                      aria-label={`${line.label}, ${formatX(point.x)}: ${formatY(point.y)}`}
                    >
                      <path {d} class={toneFill[line.tone]} />
                    </a>
                  {:else}
                    <path
                      {d}
                      class={cn(
                        toneFill[line.tone],
                        active !== undefined && active !== columnIndex && "opacity-60",
                      )}
                    />
                  {/if}
                {/if}
              {/each}
            {/each}
          {/if}
          {#if interactive && mounted.current}
            <!-- Pointer target over the plot: the nearest x value becomes active. -->
            <rect
              role="presentation"
              x={0}
              y={0}
              width={context.width}
              height={context.height}
              class="fill-transparent"
              onpointermove={(event) => {
                const offset =
                  event.offsetX *
                    (width / (event.currentTarget.ownerSVGElement?.clientWidth ?? width)) -
                  context.padding.left;
                const distances = columns.map((column) => Math.abs(px(column.x) - offset));
                active = distances.indexOf(Math.min(...distances));
              }}
              onpointerleave={() => (active = undefined)}
            />
          {/if}
        </Svg>
        {#if interactive && mounted.current && tooltip !== undefined}
          {@const left = ((px(tooltip.x) + context.padding.left) / width) * 100}
          <!-- eslint-disable svelte/no-inline-styles -- the tooltip follows the active x value -->
          <div
            class={cn(
              "pointer-events-none absolute top-2 z-dropdown grid min-w-32 gap-1 rounded-sm border border-line bg-surface-raised px-3 py-2 text-xs shadow-overlay",
              left > 60 ? "-ms-3 -translate-x-full" : "ms-3",
            )}
            style:left={`${left}%`}
            aria-hidden="true"
          >
            <!-- eslint-enable svelte/no-inline-styles -->
            <p class="font-semibold">{tooltip.heading}</p>
            {#each tooltip.rows as row (row.line.id)}
              <p class="flex items-center justify-between gap-3">
                <span class="flex items-center gap-1.5 text-fg-muted">
                  {#if series.length > 1}<svg class="size-2.5" aria-hidden="true"
                      ><rect width="10" height="10" rx="2" class={toneFill[row.line.tone]} /></svg
                    >{/if}
                  {row.line.label}
                </span>
                <span class="font-semibold tabular-nums">{row.value}</span>
              </p>
            {/each}
          </div>
        {/if}
      {/snippet}
    </ChartCore>
  {/snippet}
</Chart>
