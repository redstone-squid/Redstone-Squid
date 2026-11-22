/**
 * The site's locales, their URL prefixes, and the locale of the current page.
 *
 * Every route exists once without a prefix; middleware reads the prefix with `localeFromPath`.
 * Messages and links default to `getLocale()`, so code rarely passes a locale. Add a locale here
 * and add its catalog in this directory.
 */
export const LOCALES = {
  en: { label: "English", prefix: "", openGraph: "en_US" },
  "zh-CN": { label: "简体中文", prefix: "/zh-cn", openGraph: "zh_CN" },
} as const;
export type Locale = keyof typeof LOCALES;
export const SUPPORTED_LOCALES = Object.keys(LOCALES) as Locale[];

function isLocale(value: string): value is Locale {
  return Object.hasOwn(LOCALES, value);
}

// In the browser a page has one locale, written to <html lang> by the layout. The server
// replaces this with the request's locale; see src/server/request-locale.ts.
let source = (): Locale => {
  const lang = document.documentElement.lang;
  return isLocale(lang) ? lang : "en";
};

/** The locale of the page being rendered, on the server or in the browser. */
export function getLocale(): Locale {
  return source();
}

/** Replace where `getLocale` reads from. Only the server's request context calls this. */
export function setLocaleSource(read: () => Locale): void {
  source = read;
}

/** Read the locale from a URL prefix; paths without a known prefix are English. */
export function localeFromPath(pathname: string): Locale {
  return (
    SUPPORTED_LOCALES.find((locale) => {
      const prefix = LOCALES[locale].prefix;
      return prefix !== "" && (pathname === prefix || pathname.startsWith(`${prefix}/`));
    }) ?? "en"
  );
}

/** Replace the locale prefix while preserving the query and fragment; return a path, not an origin. */
export function localizePath(path: string, locale: Locale = getLocale()): string {
  const url = new URL(path, "https://catalogue.invalid");
  const prefix = LOCALES[localeFromPath(url.pathname)].prefix;
  const rest = url.pathname.slice(prefix.length);
  const unprefixed = rest === "" ? "/" : rest;
  const targetPrefix = LOCALES[locale].prefix;
  url.pathname =
    targetPrefix !== "" ? `${targetPrefix}${unprefixed === "/" ? "" : unprefixed}` : unprefixed;
  return `${url.pathname}${url.search}${url.hash}`;
}
