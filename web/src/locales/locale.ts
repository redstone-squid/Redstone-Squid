/**
 * The site's locales and their URL prefixes.
 *
 * Every route exists once without a prefix; middleware reads the prefix with `localeFromPath`
 * and pages read the result from `Astro.locals.locale`. Add a locale here and add its catalog
 * in this directory.
 */
export const LOCALES = {
  en: { label: "English", prefix: "", openGraph: "en_US" },
  "zh-CN": { label: "简体中文", prefix: "/zh-cn", openGraph: "zh_CN" },
} as const;
export type Locale = keyof typeof LOCALES;
export const SUPPORTED_LOCALES = Object.keys(LOCALES) as Locale[];

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
