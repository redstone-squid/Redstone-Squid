import type {
  BuildCategory,
  CreditRole,
  PublicBuildCreator,
  PublicBuildDimensions,
  PublicBuildPreview,
  RecordCriterion,
  RecordFact,
  RecordHolderSummary,
  RecordMetric,
} from "@/generated/types.gen";
import { localizePath } from "@/locales/locale";
import * as m from "@/locales/messages.gen";
import { buildKindLabel, recordFactLabel } from "@/locales/vocabulary.gen";

/**
 * What every build presentation needs. A build summary, a build detail, and a record holder
 * (through `holderBuild`) all fit.
 */
export type BuildIdentity = {
  id: number;
  title: string;
  display_name?: string | null;
  preview: PublicBuildPreview | null;
  creators?: PublicBuildCreator[];
};

export const holderBuild = (holder: RecordHolderSummary): BuildIdentity => ({
  ...holder,
  id: holder.build_id,
});

export const buildName = (build: Pick<BuildIdentity, "title" | "display_name">): string =>
  build.display_name ?? build.title;

export const buildHref = (id: number): string => localizePath(`/builds/${String(id)}`);

export const recordHref = (id: string): string => localizePath(`/records/${id}`);

/** A creator's account when they claimed one, otherwise the page for the credited name. */
export const creatorHref = (creator: Pick<PublicBuildCreator, "name" | "account_id">): string =>
  localizePath(
    creator.account_id === null
      ? `/creators/${encodeURIComponent(creator.name)}`
      : `/accounts/${creator.account_id}`,
  );

/**
 * The preview URL when it is safe to show: HTTPS or same-origin. The API should never send
 * others, but it is untrusted input.
 */
export function previewUrl(preview: { url: string } | null): string | null {
  const url = preview?.url;
  if (url === undefined) return null;
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  return URL.canParse(url) && url.startsWith("https://") ? url : null;
}

export function buildSize({ width, height, depth }: PublicBuildDimensions): string {
  const unknown = m.buildDimensionUnknown();
  return m.buildSize({
    width: width ?? unknown,
    height: height ?? unknown,
    depth: depth ?? unknown,
  });
}

/** The bounding volume in blocks, or null while any side is unknown. */
export function buildVolume({ width, height, depth }: PublicBuildDimensions): number | null {
  return width === null || height === null || depth === null ? null : width * height * depth;
}

export const categoryLabel = (category: BuildCategory): string =>
  category === "Other"
    ? m.buildCategoryOther()
    : buildKindLabel(category.toLowerCase() as Lowercase<Exclude<BuildCategory, "Other">>);

const CREDIT_ROLES = {
  creator: m.buildRoleCreator,
  designer: m.buildRoleDesigner,
  layout: m.buildRoleLayout,
  contributor: m.buildRoleContributor,
} satisfies Record<CreditRole, unknown>;

export const creditRoleLabel = (role: CreditRole): string => CREDIT_ROLES[role]();

export const ticks = (value: number | null): string =>
  value === null ? m.buildTimingUnknown() : m.buildTicks({ ticks: value });

// Record metrics and prediction deltas share these keys.
const METRIC_FACTS = {
  opening_time: "opening",
  visible_opening_time: "opening_visible",
  closing_time: "closing",
  visible_closing_time: "closing_visible",
  opening_reset_time: "opening_reset",
  closing_reset_time: "closing_reset",
  retraction_time: "retraction",
  extension_time: "extension",
  retraction_reset_time: "retraction_reset",
  extension_reset_time: "extension_reset",
  volume: "fixed_volume",
  completion_at: "completion_at",
} as const satisfies Record<string, RecordFact>;
type MetricKey = keyof typeof METRIC_FACTS;
const TIMINGS = Object.keys(METRIC_FACTS).filter((key) => key.endsWith("_time")) as MetricKey[];

const isMetricKey = (key: string): key is MetricKey => Object.hasOwn(METRIC_FACTS, key);

/** A metric key such as `opening_time` as its fact label; unknown keys read as words. */
export function metricLabel(key: string): string {
  if (isMetricKey(key)) return recordFactLabel(METRIC_FACTS[key]);
  const words = key.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A raw metric value with its unit, such as `8 gt` or `64 blocks`. */
export function metricValue(value: number | string, unit: string): string {
  if (typeof value === "string") return value;
  if (unit === "game_ticks") return m.buildTicks({ ticks: value });
  if (unit === "blocks") return m.buildBlocks({ count: value });
  return String(value);
}

/** One deciding metric, shaped for `StatGroup`. */
export type Measurement = { key: string; label: string; value: string };

/**
 * The metrics that decide a record, headline first: the completion date for "first", the
 * volume for "smallest", the first timing the record measures for "fastest", and both for
 * the combined criteria in the order they are compared ("fastest smallest" ranks by volume
 * first).
 */
export function recordMeasurements(record: {
  criterion: RecordCriterion;
  metrics?: Record<string, RecordMetric>;
}): Measurement[] {
  const metrics = record.metrics ?? {};
  const measure = (key: MetricKey | undefined): Measurement[] => {
    const metric = key === undefined ? undefined : metrics[key];
    if (key === undefined || metric === undefined) return [];
    return [{ key, label: metricLabel(key), value: metric.display }];
  };
  const speed = measure(TIMINGS.find((key) => metrics[key] !== undefined));
  const volume = measure("volume");
  switch (record.criterion) {
    case "first": {
      return measure("completion_at");
    }
    case "smallest": {
      return volume;
    }
    case "fastest": {
      return speed;
    }
    case "fastest_smallest": {
      return [...volume, ...speed];
    }
    case "smallest_fastest": {
      return [...speed, ...volume];
    }
  }
}

/** The text a catalogue `DataTable` needs; catalogue tables never group rows. */
export const tableLabels = () => ({
  expand: m.catalogueTableExpand(),
  collapse: m.catalogueTableCollapse(),
  visibility: {
    menu: m.catalogueTableColumns(),
    show: (column: string) => m.catalogueTableShow({ column }),
    hide: (column: string) => m.catalogueTableHide({ column }),
  },
});
