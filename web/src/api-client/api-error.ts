import type { FieldErrorCode, ValidationIssue } from "@/generated/types.gen";
import { z } from "zod";

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

const FIELD_ERROR_CODES = [
  "rule_failed",
  "invalid_value",
  "required",
  "wrong_type",
  "unknown_field",
  "removed_field",
  "unknown_option",
  "required_value",
  "incompatible_fields",
  "below_minimum",
  "above_maximum",
  "too_short",
  "too_long",
  "too_few_items",
  "too_many_items",
] as const satisfies readonly FieldErrorCode[];

// Only the discriminant is checked: a formatter switches on `code` and reads the params that
// code's variant declares. An issue with a code this build does not know is dropped, so
// exhaustive formatters stay exhaustive; the problem's detail still explains it.
const validationIssue = z.looseObject({ field: z.string(), code: z.enum(FIELD_ERROR_CODES) });

/** A failed API call. Unexpected failures such as timeouts become a 503 without a problem body. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: ApiProblemDetail | undefined;
  readonly requestId: string | null;
  /** No response arrived: the network failed or the visitor is offline. Worth retrying. */
  readonly offline: boolean;
  readonly retryAfter: string | null;

  constructor(
    status: number,
    problem?: ApiProblemDetail,
    options?: ErrorOptions & {
      requestId?: string | null;
      offline?: boolean;
      retryAfter?: string | null;
    },
  ) {
    super(problem?.detail ?? problem?.title ?? "API request failed.", options);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
    this.requestId = options?.requestId ?? null;
    this.retryAfter = options?.retryAfter ?? null;
    this.offline = options?.offline ?? false;
  }

  static from(error: unknown): ApiError {
    return error instanceof ApiError ? error : new ApiError(503, undefined, { cause: error });
  }

  /**
   * The resource changed since the caller read it. Context carries the caller's `expected`
   * revision and the stored `current` one when the server knows them.
   */
  get conflict(): boolean {
    return this.problem?.code === "REVISION_CONFLICT";
  }

  /**
   * The field issues of a validation problem. `field` is a domain field id or a JSON Pointer
   * into the request; several issues can name one control, and request-wide failures have none.
   */
  get fieldErrors(): ValidationIssue[] {
    const fields = this.problem?.context?.fields;
    if (!Array.isArray(fields)) return [];
    return fields.flatMap((issue) => {
      const parsed = validationIssue.safeParse(issue);
      // The code was checked above; the variant's params come from the API's own schema.
      return parsed.success ? [parsed.data as ValidationIssue] : [];
    });
  }
}

/** What a generated SDK call resolves to. */
export type ApiResult<T> = { data: T | undefined; error?: unknown; response?: Response };

/**
 * Return the data of a generated SDK call or throw an `ApiError`. The generated client resolves
 * a network failure without a response, which becomes an offline 503.
 *
 * Example: `unwrap(await recordsGet({ client: Astro.locals.api, path: { record_id: id } }))`.
 */
export function unwrap<T>(result: ApiResult<T>): T {
  if (result.data !== undefined) return result.data;
  const problem = problemDetail.safeParse(result.error).data;
  throw new ApiError(result.response?.status ?? problem?.status ?? 503, problem, {
    retryAfter: result.response?.headers.get("Retry-After"),
    requestId: result.response?.headers.get("Request-Id") ?? null,
    offline: result.response === undefined,
    cause: result.error,
  });
}
