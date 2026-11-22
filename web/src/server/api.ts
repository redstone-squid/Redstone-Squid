import { createClient, type Client } from "@/generated/client";
import type { Locale } from "@/locales/locale";
import { context, propagation, SpanKind } from "@opentelemetry/api";
import { z } from "zod";
import type { RuntimeConfig } from "./config";
import { traceHttp } from "./telemetry";

const API_TIMEOUT_MS = 5_000;

// An RFC 9457 error body. Validated rather than taken from the generated types because error
// responses are untrusted, and `code` stays a string so a newer API can add codes.
const problemDetail = z.object({
  title: z.string(),
  status: z.int().min(100).max(599),
  type: z.string().optional(),
  detail: z.string().nullish(),
  instance: z.string().nullish(),
  code: z.string().nullish(),
  resource: z.string().nullish(),
  context: z.record(z.string(), z.unknown()).nullish(),
});
export type ApiProblemDetail = z.infer<typeof problemDetail>;

/** A failed API call. Unexpected failures such as timeouts become a 503 without a problem body. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: ApiProblemDetail | undefined;
  readonly requestId: string | null;

  constructor(
    status: number,
    problem?: ApiProblemDetail,
    options?: ErrorOptions & { requestId?: string | null },
  ) {
    super(problem?.detail ?? problem?.title ?? "API request failed.", options);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
    this.requestId = options?.requestId ?? null;
  }

  static from(error: unknown): ApiError {
    return error instanceof ApiError ? error : new ApiError(503, undefined, { cause: error });
  }
}

/** What a generated SDK call resolves to. */
type ApiResult<T> = { data: T | undefined; error?: unknown; response?: Response };

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

/**
 * Return the data of a generated SDK call or throw an `ApiError`.
 *
 * Example: `unwrap(await recordsGet({ client: Astro.locals.api, path: { record_id: id } }))`.
 */
export function unwrap<T>(result: ApiResult<T>): T {
  if (result.data !== undefined) return result.data;
  const problem = problemDetail.safeParse(result.error).data;
  throw new ApiError(result.response?.status ?? problem?.status ?? 503, problem, {
    requestId: result.response?.headers.get("Request-Id") ?? null,
  });
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
