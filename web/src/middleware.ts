import { defineMiddleware } from "astro:middleware";
import { localeFromPath, localizePath } from "@/locales/locale";
import { createApiClient } from "@/server/api";
import { getRuntimeConfig } from "@/server/config";
import { withRequestLocale } from "@/server/request-locale";

/**
 * Set up request locals and log each request.
 *
 * Every route exists once, without a locale prefix. A prefixed request such as `/zh-cn/records`
 * is rewritten to `/records` with `locals.locale` set, so pages never branch on the URL prefix.
 * Configuration is read here so a bad deployment fails with a 500 before the page starts
 * streaming, rather than truncating a 200 response.
 */
export const onRequest = defineMiddleware(async (context, next) => {
  const startedAt = performance.now();
  const { pathname, search } = context.url;
  const prefixed = localeFromPath(pathname);
  // Astro.rewrite() in a page runs middleware again with an unprefixed URL, such as /404.
  // Locals are only partly set on the first pass, so read them as partial.
  const inherited = (context.locals as Partial<App.Locals>).locale;
  const locale = prefixed === "en" ? (inherited ?? "en") : prefixed;
  context.locals.locale = locale;
  const log = (event: string, fields: Record<string, unknown>) =>
    JSON.stringify({
      event,
      method: context.request.method,
      path: pathname,
      duration_ms: Math.round(performance.now() - startedAt),
      ...fields,
    });
  try {
    context.locals.config = getRuntimeConfig();
    context.locals.api = createApiClient(locale, context.locals.config);
    const response = await withRequestLocale(locale, () =>
      prefixed === "en" ? next() : next(localizePath(pathname, "en") + search),
    );
    console.info(log("http_request", { status: response.status }));
    return response;
  } catch (error) {
    console.error(
      log("http_request_error", { error: error instanceof Error ? error.message : String(error) }),
    );
    throw error;
  }
});
