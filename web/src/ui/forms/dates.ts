import {
  CalendarDate,
  CalendarDateTime,
  parseDate,
  parseDateTime,
  parseTime,
  type DateValue,
  type Time,
} from "@internationalized/date";
import type { fieldAttributes } from "./field";

// The date fields hold ISO strings, the same text a native date input submits, so features
// never see the picker's value types. A time drops its seconds to match `datetime-local`.

/** `YYYY-MM-DD`, or `YYYY-MM-DDTHH:mm` with `time`. Empty or malformed text gives undefined. */
export function parseDateText(text: string | undefined, time: boolean): DateValue | undefined {
  if (text === undefined || text === "") return undefined;
  try {
    return time ? parseDateTime(text) : parseDate(text.slice(0, 10));
  } catch {
    return undefined;
  }
}

export function formatDateText(value: DateValue | undefined): string {
  if (value === undefined) return "";
  const day = new CalendarDate(value.year, value.month, value.day).toString();
  return value instanceof CalendarDateTime ? `${day}T${clock(value)}` : day;
}

/** `HH:mm`. Empty or malformed text gives undefined. */
export function parseTimeText(text: string | undefined): Time | undefined {
  if (text === undefined || text === "") return undefined;
  try {
    return parseTime(text);
  } catch {
    return undefined;
  }
}

export function formatTimeText(value: Time | undefined): string {
  return value === undefined ? "" : clock(value);
}

function clock({ hour, minute }: { hour: number; minute: number }): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

/**
 * The locale of the page, which orders the segments and names months and weekdays. The
 * pickers only render in the browser, after hydration, so `<html lang>` is always there.
 */
export function pageLocale(): string {
  const { lang } = document.documentElement;
  return lang === "" ? "en" : lang;
}

/** Names of the segments a date or time is typed into, read by screen readers. */
export type DateSegmentLabels = {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
  dayPeriod: string;
  /** Read for a segment with nothing typed in it yet. */
  empty: string;
};

/** The text every date field needs: segment names and the calendar's buttons. */
export type DateLabels = {
  segments: DateSegmentLabels;
  /** Names the button that opens the calendar, and the calendar itself. */
  calendar: string;
  previousMonth: string;
  nextMonth: string;
};

/**
 * Attributes that replace the ones bits-ui puts on a segment, whose names are hard-coded
 * English ("year, ", "Empty") and which ignore the field's hint and error. The group around
 * the segments takes the field's label, so each segment needs only its own name.
 */
export function segmentAttributes(
  part: string,
  props: Record<string, unknown>,
  labels: DateSegmentLabels,
  field: ReturnType<typeof fieldAttributes>,
): Record<string, unknown> {
  if (part === "literal") return props;
  const name = part in labels ? labels[part as keyof DateSegmentLabels] : undefined;
  const empty = props["aria-valuetext"] === "Empty";
  return {
    ...props,
    "aria-labelledby": undefined,
    "aria-label": name ?? props["aria-label"],
    "aria-valuetext": empty ? labels.empty : props["aria-valuetext"],
    "data-empty": empty ? "" : undefined,
    "aria-describedby": field["aria-describedby"],
    "aria-invalid": [field["aria-invalid"], props["aria-invalid"]].some(
      (invalid) => invalid === true || invalid === "true",
    )
      ? "true"
      : undefined,
  };
}
