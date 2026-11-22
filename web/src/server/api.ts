import { createClient, type Client } from "@/generated/client";
import type { Locale } from "@/locales/locale";
import { decodeProblemDetail, type ApiProblemDetail } from "@/lib/problem-details";
import type { RuntimeConfig } from "./config";

const API_TIMEOUT_MS = 5_000;

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
  const problem = decodeProblemDetail(result.error);
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
