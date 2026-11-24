import type { ChartTone } from "./styles";

export type { ChartTone };

/** The data behind a chart, shown as a table under "Show data". */
export type ChartTable = {
  caption: string;
  columns: { key: string; label: string; numeric?: boolean }[];
  rows: Record<string, string | number>[];
};

export type ChartStatus = "ready" | "loading" | "empty" | "error";

/** An x or y axis. `format` turns a value into its tick and table text. */
export type ChartAxis<Value> = {
  label: string;
  format?: (value: Value) => string;
  domain?: [Value, Value];
};

export type SeriesPoint = { x: number | Date | string; y: number; href?: string };
export type Series = { id: string; label: string; tone: ChartTone; points: SeriesPoint[] };

export type RangeSegment = {
  from: number;
  /** `null` runs past the end of the axis. */
  to: number | null;
  /** Only every `step`th width counts, starting from `residue`, such as odd widths only. */
  step?: number;
  residue?: number;
  value: number;
  label: string;
  tone?: ChartTone;
  href?: string;
};
