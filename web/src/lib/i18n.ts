import { FluentBundle, FluentResource } from "@fluent/bundle";
import english from "../locales/en.ftl?raw";
import chinese from "../locales/zh-CN.ftl?raw";
import {
  messageKeys,
  type MessageKey,
  type MessageVariables,
  type StaticMessageKey,
} from "../locales/keys";

export type { MessageKey, StaticMessageKey } from "../locales/keys";
export const LOCALES = {
  en: { label: "English", prefix: "", openGraph: "en_US" },
  "zh-CN": { label: "简体中文", prefix: "/zh-cn", openGraph: "zh_CN" },
} as const;
export type Locale = keyof typeof LOCALES;
export const SUPPORTED_LOCALES = Object.keys(LOCALES) as Locale[];

function createBundle(locale: Locale, source: string): FluentBundle {
  const bundle = new FluentBundle(locale, { useIsolating: false });
  const errors = bundle.addResource(new FluentResource(source));
  if (errors.length > 0) throw new AggregateError(errors, `Invalid ${locale} catalog`);
  return bundle;
}
const bundles = {
  en: createBundle("en", english),
  "zh-CN": createBundle("zh-CN", chinese),
} satisfies Record<Locale, FluentBundle>;
const knownKeys = new Set<string>(messageKeys);

/** Read the locale from a URL prefix. Middleware does this once per request; use `Astro.locals.locale`. */
export function localeFromPath(pathname: string): Locale {
  return (
    SUPPORTED_LOCALES.find((locale) => {
      const prefix = LOCALES[locale].prefix;
      return prefix !== "" && (pathname === prefix || pathname.startsWith(`${prefix}/`));
    }) ?? "en"
  );
}

/** Replace the locale prefix while preserving the query and fragment; return a path, not an origin. */
export function localizePath(path: string, locale: Locale): string {
  const url = new URL(path, "https://catalogue.invalid");
  const prefix = LOCALES[localeFromPath(url.pathname)].prefix;
  const rest = url.pathname.slice(prefix.length);
  const unprefixed = rest === "" ? "/" : rest;
  const targetPrefix = LOCALES[locale].prefix;
  url.pathname =
    targetPrefix !== "" ? `${targetPrefix}${unprefixed === "/" ? "" : unprefixed}` : unprefixed;
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Format catalog messages in Astro and browser islands; fall back to English for incomplete translations. */
export function translate(locale: Locale, key: StaticMessageKey): string;
export function translate<K extends keyof MessageVariables>(
  locale: Locale,
  key: K,
  variables: MessageVariables[K],
): string;
export function translate(
  locale: Locale,
  key: MessageKey,
  variables: Record<string, string | number> = {},
): string {
  for (const candidate of locale === "en" ? (["en"] as const) : ([locale, "en"] as const)) {
    const bundle = bundles[candidate];
    const message = bundle.getMessage(key.replaceAll(".", "-"));
    if (message?.value == null) continue;
    const errors: Error[] = [];
    const result = bundle.formatPattern(message.value, variables, errors);
    if (errors.length === 0) return result;
  }
  throw new Error(`Unable to format translation: ${locale}/${key}`);
}

export function translateTaxonomy(locale: Locale, key: string, fallback?: string): string {
  const messageKey = `taxonomy.${key.toLowerCase()}`;
  return knownKeys.has(messageKey)
    ? translate(locale, messageKey as StaticMessageKey)
    : (fallback ?? key);
}

export function formatDate(locale: Locale, value: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: "UTC" }).format(
    new Date(value),
  );
}

export function formatNumber(locale: Locale, value: number): string {
  return new Intl.NumberFormat(locale).format(value);
}
