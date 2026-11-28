import { formatList } from "@/locales/format";
import * as m from "@/locales/messages.gen";
import { recordFactLabel } from "@/locales/vocabulary.gen";

const STATUSES = {
  approved: { label: m.creatorClaimApproved, tone: "success" },
  rejected: { label: m.creatorClaimRejected, tone: "danger" },
  cancelled: { label: m.submissionCancelled, tone: "neutral" },
  stale: { label: m.submissionReviewStale, tone: "warning" },
  pending: { label: m.creatorClaimPending, tone: "info" },
} as const;

const status = (value: string) =>
  Object.hasOwn(STATUSES, value) ? STATUSES[value as keyof typeof STATUSES] : STATUSES.pending;

export const statusLabel = (value: string): string => status(value).label();

/** The `Badge` tone for a submission status. */
export const statusTone = (value: string) => status(value).tone;

const FIELD_LABELS: Record<string, () => string> = {
  title: m.submissionFieldTitle,
  description: m.submissionFieldDescription,
  creators: m.buildCreators,
  dimensions: m.buildDimensions,
  door_dimensions: m.buildDoorSize,
  opening_time: m.buildOpening,
  closing_time: m.buildClosing,
  visible_opening_time: m.buildVisibleOpening,
  visible_closing_time: m.buildVisibleClosing,
  extension_time: m.buildExtension,
  retraction_time: m.buildRetraction,
  versions: m.recordVersionScope,
  completion_date: () => recordFactLabel("completion_at"),
};

/** A submitted field's label; fields this site does not know read as words. */
function fieldLabel(key: string): string {
  const known = FIELD_LABELS[key];
  if (known) return known();
  const words = key.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function display(key: string, value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "boolean") return value ? m.commonYes() : m.commonNo();
  if (typeof value === "number" && key.endsWith("_time")) return m.buildTicks({ ticks: value });
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (isRecord(value) && ["width", "height", "depth"].every((side) => side in value)) {
    const side = (name: string) => {
      const length = value[name];
      return typeof length === "number" ? length : m.buildDimensionUnknown();
    };
    return m.buildSize({ width: side("width"), height: side("height"), depth: side("depth") });
  }
  if (Array.isArray(value)) {
    const items = value.flatMap((item): string[] => {
      if (isRecord(item) && typeof item.name === "string") return [item.name];
      return typeof item === "object" || item == null ? [] : [String(item)];
    });
    return items.length > 0 ? formatList(items) : null;
  }
  return null;
}

/**
 * Submitted facts as labelled rows: sizes read "9 × 7 × 4", timings carry their unit, credit
 * lists read as names, and nested groups prefix their fields with the group's label. Empty
 * values are left out; zero and false stay.
 */
export function factRows(
  facts: Record<string, unknown>,
  parent = "",
): { key: string; term: string; value: string }[] {
  return Object.entries(facts).flatMap(([key, value]) => {
    const term = parent === "" ? fieldLabel(key) : `${parent} · ${fieldLabel(key)}`;
    const shown = display(key, value);
    if (shown !== null)
      return [{ key: parent === "" ? key : `${parent}.${key}`, term, value: shown }];
    return isRecord(value) ? factRows(value, term) : [];
  });
}
