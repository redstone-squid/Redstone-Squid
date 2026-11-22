import { createClient, type Client } from "@/generated/client";
import type { Locale } from "@/locales/locale";
import { z } from "zod";
import type { RuntimeConfig } from "./config";

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

/**
 * Return the data of a generated SDK call or throw an `ApiError`.
 *
 * Example: `unwrap(await recordsGet({ client: Astro.locals.api, path: { record_id: id } }))`.
 */
export function unwrap<T>(result: {
  data: T | undefined;
  error?: unknown;
  response?: Response;
}): T {
  if (result.data !== undefined) return result.data;
  const problem = problemDetail.safeParse(result.error).data;
  throw new ApiError(result.response?.status ?? problem?.status ?? 503, problem, {
    requestId: result.response?.headers.get("Request-Id") ?? null,
  });
}

function timedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const timeout = AbortSignal.timeout(API_TIMEOUT_MS);
  const signal = init?.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
  return fetch(input, { ...init, signal });
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
