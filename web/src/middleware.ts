import { SpanKind, TraceFlags, trace } from "@opentelemetry/api";
import { defineMiddleware, sequence } from "astro:middleware";
import { DEFAULT_LOCALE, localeFromPath, localizePath, unprefixedPath } from "@/locales/locale";
import { createApiClient } from "@/server/api";
import { readPreference } from "@/server/cookies";
import { getRuntimeConfig } from "@/server/config";
import { loadSession } from "@/server/session";
import { findRedirect } from "@/server/redirects";
import { withRequestLocale } from "@/server/request-locale";
import { traceHttp } from "@/server/telemetry";
import { parseContrast, parseTheme } from "@/ui/themes";

const traceRequest = defineMiddleware((context, next) => {
  if (context.routePattern.startsWith("/telemetry/")) return next();
  return traceHttp(
    "catalogue request",
    SpanKind.SERVER,
    {
      "http.request.method": context.request.method,
      "http.route": context.routePattern,
    },
    async () => {
      const response = await next();
      const span = trace.getActiveSpan()?.spanContext();
      if (
        span?.traceFlags === TraceFlags.SAMPLED &&
        response.headers.get("Content-Type")?.startsWith("text/html") === true
      ) {
        response.headers.append(
          "Server-Timing",
          `squid;desc="00-${span.traceId}-${span.spanId}-01"`,
        );
      }
      return response;
    },
  );
});

/**
 * Set up request locals and log each request.
 *
 * Every route exists once, without a locale prefix. A prefixed request such as `/zh-cn/records`
 * is rewritten to `/records` with `locals.locale` set, so pages never branch on the URL prefix.
 * Configuration is read here so a bad deployment fails with a 500 before the page starts
 * streaming, rather than truncating a 200 response.
 */
const catalogueRequest = defineMiddleware(async (context, next) => {
  if (context.routePattern.startsWith("/telemetry/")) return next();
  const startedAt = performance.now();
  const { pathname, search } = context.url;
  const prefixed = localeFromPath(pathname);
  // Astro.rewrite() in a page runs middleware again with an unprefixed URL, such as /404.
  // Locals are only partly set on the first pass, so read them as partial.
  const inherited = context.locals as Partial<App.Locals>;
  const locale = prefixed ?? inherited.locale ?? DEFAULT_LOCALE;
  context.locals.locale = locale;
  context.locals.requestedPath = inherited.requestedPath ?? `${pathname}${search}`;
  // The service worker precaches /offline once, so it follows the device rather than
  // keeping whatever was pinned at install time.
  const followDevice = unprefixedPath(pathname) === "/offline";
  context.locals.theme = followDevice ? undefined : parseTheme(readPreference(context, "theme"));
  context.locals.contrast = followDevice
    ? undefined
    : parseContrast(readPreference(context, "contrast"));
  const log = (event: string, fields: Record<string, unknown>) =>
    JSON.stringify({
      event,
      method: context.request.method,
      path: pathname,
      duration_ms: Math.round(performance.now() - startedAt),
      ...fields,
    });
  // A moved route answers in the request's locale, before any rendering.
  const moved = findRedirect(unprefixedPath(pathname));
  if (moved !== undefined) return context.redirect(localizePath(`${moved}${search}`, locale), 308);
  try {
    context.locals.config = getRuntimeConfig();
    context.locals.api = createApiClient(locale, context.locals.config);
    // A rewrite to an error page keeps the session the first pass loaded.
    context.locals.session =
      inherited.session === undefined ? await loadSession(context) : inherited.session;
    const response = await withRequestLocale(locale, () =>
      prefixed === undefined ? next() : next(unprefixedPath(pathname) + search),
    );
    // A page that knows who is signed in must not be stored by a shared cache.
    if (context.locals.session !== null) response.headers.set("Cache-Control", "private, no-store");
    console.info(log("http_request", { status: response.status }));
    return response;
  } catch (error) {
    console.error(
      log("http_request_error", { error: error instanceof Error ? error.message : String(error) }),
    );
    throw error;
  }
});

/** Headers that complement the content security policy in `astro.config.ts`. */
const securityHeaders = defineMiddleware(async (_context, next) => {
  const response = await next();
  const headers = response.headers;
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  // Browsers ignore this over plain HTTP, so development is unaffected. Subdomains are left
  // out because the host is shared with other services.
  headers.set("Strict-Transport-Security", "max-age=31536000");
  // Nothing on the site opens or is opened by another origin's window, or embeds our files.
  headers.set("Cross-Origin-Opener-Policy", "same-origin");
  headers.set("Cross-Origin-Resource-Policy", "same-origin");
  headers.set("Permissions-Policy", DENIED_FEATURES.map((feature) => `${feature}=()`).join(", "));
  // The policy's `report-to csp` posts violations here; see src/pages/telemetry/csp.ts.
  headers.set("Reporting-Endpoints", 'csp="/telemetry/csp"');
  return response;
});

/** Powerful browser features the site never uses, denied so injected code cannot ask. */
const DENIED_FEATURES = [
  "accelerometer",
  "browsing-topics",
  "camera",
  "display-capture",
  "geolocation",
  "gyroscope",
  "hid",
  "magnetometer",
  "microphone",
  "midi",
  "payment",
  "serial",
  "usb",
];

export const onRequest = sequence(securityHeaders, traceRequest, catalogueRequest);
