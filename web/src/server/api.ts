import { ApiError, unwrap, type ApiResult } from "@/api-client/api-error";
import { createClient, type Client } from "@/generated/client";
import type { Locale } from "@/locales/locale";
import { context, propagation, SpanKind } from "@opentelemetry/api";
import type { RuntimeConfig } from "./config";
import { traceHttp } from "./telemetry";

const API_TIMEOUT_MS = 5_000;

/** Load data before a page streams. Middleware renders API failures. */
export async function load<T>(call: Promise<ApiResult<T>>): Promise<T> {
  try {
    return unwrap(await call);
  } catch (caught) {
    throw ApiError.from(caught);
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

/**
 * Create the per-request client that middleware stores as `Astro.locals.api`.
 *
 * `forwardedFor` is the `X-Forwarded-For` chain naming the visitor. Every page calls the API
 * from this server, so without it the API would count all visitors against one IP quota. The
 * API trusts the chain only from addresses in `SQUID_API_TRUSTED_PROXY_IPS`.
 */
export function createApiClient(
  locale: Locale,
  config: RuntimeConfig,
  forwardedFor: string | undefined,
): Client {
  return createClient({
    baseUrl: config.apiInternalUrl,
    fetch: timedFetch,
    headers: {
      "Accept-Language": locale,
      ...(forwardedFor === undefined ? {} : { "X-Forwarded-For": forwardedFor }),
    },
    responseStyle: "fields",
  });
}

/**
 * The `X-Forwarded-For` chain to send upstream: the one this request arrived with, plus the
 * address it came from, as any proxy appends. The API reads the chain from the right and stops
 * at the first address it does not trust, so a visitor who sends their own header cannot
 * choose the address the API sees.
 */
export function forwardedFor(request: Request, peer: string | undefined): string | undefined {
  const chain = [request.headers.get("X-Forwarded-For")?.trim(), peer].filter(
    (entry): entry is string => entry !== undefined && entry !== "",
  );
  return chain.length === 0 ? undefined : chain.join(", ");
}
