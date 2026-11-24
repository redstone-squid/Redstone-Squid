import { ApiError, unwrap, type ApiResult } from "@/api-client/api-error";
import { createClient, type Client } from "@/generated/client";
import type { Locale } from "@/locales/locale";
import { context, propagation, SpanKind } from "@opentelemetry/api";
import type { RuntimeConfig } from "./config";
import { traceHttp } from "./telemetry";

const API_TIMEOUT_MS = 5_000;

/** The data a page loaded, or the status page it should render instead. */
export type Loaded<T> = { data: T } | { status: 404 | 503 };

/**
 * Await a generated SDK call for a page. An API 404 stays a 404; any other failure, including a
 * timeout, is logged and becomes 503, so the visitor sees a status page rather than a crash:
 *
 *     const loaded = await load(recordsGet({ client: Astro.locals.api, path }));
 *     if ("status" in loaded) return Astro.rewrite(`/${loaded.status}`);
 */
export async function load<T>(call: Promise<ApiResult<T>>): Promise<Loaded<T>> {
  try {
    return { data: unwrap(await call) };
  } catch (caught) {
    const error = ApiError.from(caught);
    if (error.status === 404) return { status: 404 };
    console.error(
      JSON.stringify({
        event: "api_failure",
        status: error.status,
        request_id: error.requestId,
        error: error.message,
      }),
    );
    return { status: 503 };
  }
}

function timedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = new Request(input, init);
  return traceHttp(
    "catalogue API request",
    SpanKind.CLIENT,
    {
      "http.request.method": request.method,
      "server.address": new URL(request.url).hostname,
    },
    () => {
      propagation.inject(context.active(), request.headers, {
        set: (headers, key, value) => headers.set(key, value),
      });
      const signal = AbortSignal.any([request.signal, AbortSignal.timeout(API_TIMEOUT_MS)]);
      return fetch(request, { signal });
    },
  );
}

/** Create the per-request client that middleware stores as `Astro.locals.api`. */
export function createApiClient(locale: Locale, config: RuntimeConfig): Client {
  return createClient({
    baseUrl: config.apiBaseUrl,
    fetch: timedFetch,
    headers: { "Accept-Language": locale },
    responseStyle: "fields",
  });
}
