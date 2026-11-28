import { getLocale, type Locale } from "./locale";

// Generated message functions call these. Formatters are cached per locale and options.
const numberFormats = new Map<string, Intl.NumberFormat>();
const dateFormats = new Map<string, Intl.DateTimeFormat>();
const pluralRules = new Map<string, Intl.PluralRules>();
const listFormats = new Map<string, Intl.ListFormat>();

function cached<T>(cache: Map<string, T>, key: string, create: () => T): T {
  let value = cache.get(key);
  if (value === undefined) {
    value = create();
    cache.set(key, value);
  }
  return value;
}

export function formatNumber(
  locale: Locale,
  value: number,
  options: Intl.NumberFormatOptions = {},
): string {
  const key = `${locale}|${JSON.stringify(options)}`;
  return cached(numberFormats, key, () => new Intl.NumberFormat(locale, options)).format(value);
}

/** Format a date or ISO string. Dates show in UTC unless the message names a time zone. */
export function formatDateTime(
  locale: Locale,
  value: Date | string,
  options: Intl.DateTimeFormatOptions = {},
): string {
  const resolved = { timeZone: "UTC", ...options };
  const key = `${locale}|${JSON.stringify(resolved)}`;
  return cached(dateFormats, key, () => new Intl.DateTimeFormat(locale, resolved)).format(
    new Date(value),
  );
}

/** Join names or other items the way the locale writes a list: "A, B and C", "A、B和C". */
export function formatList(items: string[], locale: Locale = getLocale()): string {
  return cached(listFormats, locale, () => new Intl.ListFormat(locale)).format(items);
}

/** The pieces of `formatList`, so each item can render as its own element, such as a link. */
export function formatListParts(
  items: string[],
  locale: Locale = getLocale(),
): { type: "element" | "literal"; value: string }[] {
  return cached(listFormats, locale, () => new Intl.ListFormat(locale)).formatToParts(items);
}

/** Format a variable used as plain text: numbers get locale grouping, strings pass through. */
export function formatValue(locale: Locale, value: string | number): string {
  return typeof value === "number" ? formatNumber(locale, value) : value;
}

/**
 * Pick a select-expression variant the way Fluent does: an exact key match first, then the
 * number's plural category, then the default variant.
 */
export function select(
  locale: Locale,
  value: string | number,
  variants: Partial<Record<string, string>>,
  fallback: string,
  options: Intl.PluralRulesOptions = {},
): string {
  const exact = variants[String(value)];
  if (exact !== undefined) return exact;
  if (typeof value === "number") {
    const key = `${locale}|${JSON.stringify(options)}`;
    const category = cached(pluralRules, key, () => new Intl.PluralRules(locale, options)).select(
      value,
    );
    const plural = variants[category];
    if (plural !== undefined) return plural;
  }
  return variants[fallback] ?? "";
}
