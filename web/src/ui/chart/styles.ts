/**
 * Series colors, by tone. Numbered tones are the categorical palette, assigned in order and
 * kept with an entity across filters; `accent` marks the one series a chart is about and
 * `muted` a baseline or "other". Text never takes these colors.
 */
export type ChartTone = 1 | 2 | 3 | 4 | 5 | 6 | "accent" | "muted";

export const toneFill: Record<ChartTone, string> = {
  1: "fill-chart-1",
  2: "fill-chart-2",
  3: "fill-chart-3",
  4: "fill-chart-4",
  5: "fill-chart-5",
  6: "fill-chart-6",
  accent: "fill-accent",
  muted: "fill-line-strong",
};

export const toneStroke: Record<ChartTone, string> = {
  1: "stroke-chart-1",
  2: "stroke-chart-2",
  3: "stroke-chart-3",
  4: "stroke-chart-4",
  5: "stroke-chart-5",
  6: "stroke-chart-6",
  accent: "stroke-accent",
  muted: "stroke-line-strong",
};

export const toneBackground: Record<ChartTone, string> = {
  1: "bg-chart-1",
  2: "bg-chart-2",
  3: "bg-chart-3",
  4: "bg-chart-4",
  5: "bg-chart-5",
  6: "bg-chart-6",
  accent: "bg-accent",
  muted: "bg-line-strong",
};

/** Axis text and rules, applied through LayerChart's `classes`. */
export const axisClasses = {
  tickLabel: "fill-fg-muted text-xs",
  label: "fill-fg-muted text-xs font-semibold",
  rule: "stroke-line-strong",
  tick: "stroke-line-strong",
};
