/**
 * The site's locales, their URL prefixes, and the locale of the current page.
 *
 * Every route exists once; the default locale has no prefix and middleware reads the others
 * with `localeFromPath`. Messages and links default to `getLocale()`, so code rarely passes a
 * locale. To add a language, add it here and add its catalog in this directory.
 *
 * `en-XA` is a pseudo-locale compiled from English: accented, bracketed, and about 40% longer,
 * so untranslated text and layouts that cannot grow stand out. It is never listed or indexed.
 */
export const LOCALES = {
  en: { label: "English", prefix: "", openGraph: "en_US", pseudo: false },
  "zh-CN": { label: "简体中文", prefix: "/zh-cn", openGraph: "zh_CN", pseudo: false },
  "en-XA": { label: "[Ƥšéûđö]", prefix: "/en-xa", openGraph: "en_US", pseudo: true },
} as const;
export type Locale = keyof typeof LOCALES;
/** The unprefixed locale that catalogs are written in and translated from. */
export const DEFAULT_LOCALE = "en" satisfies Locale;
export const SUPPORTED_LOCALES = Object.keys(LOCALES) as Locale[];
/** Locales with a catalog, offered to visitors and search engines. */
export const PUBLIC_LOCALES = SUPPORTED_LOCALES.filter((locale) => !LOCALES[locale].pseudo);

function isLocale(value: string): value is Locale {
  return Object.hasOwn(LOCALES, value);
}

// In the browser a page has one locale, written to <html lang> by the layout. The server
// replaces this with the request's locale; see src/server/request-locale.ts.
let source = (): Locale => {
  const lang = document.documentElement.lang;
  if (!isLocale(lang)) throw new Error(`<html lang="${lang}"> is not a supported locale.`);
  return lang;
};

/** The locale of the page being rendered, on the server or in the browser. */
export function getLocale(): Locale {
  return source();
}

/** Replace where `getLocale` reads from. Only the server's request context calls this. */
export function setLocaleSource(read: () => Locale): void {
  source = read;
}

/** Return the locale a URL prefix names, or undefined for an unprefixed path. */
export function localeFromPath(pathname: string): Locale | undefined {
  return SUPPORTED_LOCALES.find((locale) => {
    const prefix = LOCALES[locale].prefix;
    return prefix !== "" && (pathname === prefix || pathname.startsWith(`${prefix}/`));
  });
}

/** Replace the locale prefix while preserving the query and fragment; return a path, not an origin. */
export function localizePath(path: string, locale: Locale = getLocale()): string {
  const url = new URL(path, "https://catalogue.invalid");
  const prefix = LOCALES[localeFromPath(url.pathname) ?? DEFAULT_LOCALE].prefix;
  const rest = url.pathname.slice(prefix.length);
  const unprefixed = rest === "" ? "/" : rest;
  const targetPrefix = LOCALES[locale].prefix;
  url.pathname =
    targetPrefix !== "" ? `${targetPrefix}${unprefixed === "/" ? "" : unprefixed}` : unprefixed;
  return `${url.pathname}${url.search}${url.hash}`;
}

/** The route a path addresses, without its locale prefix. */
export function unprefixedPath(path: string): string {
  return localizePath(path, DEFAULT_LOCALE);
}
