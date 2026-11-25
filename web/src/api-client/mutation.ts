import { createClient, type Client } from "@/generated/client";
import { browserCsrfGet } from "@/generated/sdk.gen";
import type { ErrorCode } from "@/generated/types.gen";
import { getLocale } from "@/locales/locale";
import * as m from "@/locales/messages.gen";
import { ApiError, unwrap, type ApiResult } from "./api-error";

/** How one write identifies itself. Reuse `idempotencyKey` when retrying the same write. */
export type MutationOptions = {
  idempotencyKey: string;
  /** The ETag the caller read, sent as If-Match so a stale write fails instead of overwriting. */
  etag?: string | null;
};

/**
 * The browser's connection to the API for one page, shared by its islands.
 *
 * Islands call the API at `baseUrl`, which in production is the site's own origin: the gateway
 * routes `/v1` to the API, so the session cookie is first-party and the CSP needs no extra
 * `connect-src`. See "Same-origin API" in docs/plans/web-components/README.md.
 */
export class BrowserApi {
  readonly client: Client;
  #csrf: Promise<string> | undefined;

  constructor(baseUrl: string) {
    this.client = createClient({
      baseUrl,
      credentials: "include",
      headers: { "Accept-Language": getLocale() },
      responseStyle: "fields",
    });
  }

  /**
   * Send a write with the CSRF token, Idempotency-Key and If-Match headers, and return its data
   * and new ETag. Throws `ApiError`; `error.offline` means retry with the same key.
   */
  async mutate<T>(
    send: (headers: Record<string, string>) => Promise<ApiResult<T>>,
    options: MutationOptions,
  ): Promise<{ data: T; etag: string | null }> {
    const headers: Record<string, string> = {
      "CSRF-Token": await this.#csrfToken(),
      "Idempotency-Key": options.idempotencyKey,
    };
    if (options.etag != null) headers["If-Match"] = options.etag;
    const result = await send(headers);
    return { data: unwrap(result), etag: result.response?.headers.get("ETag") ?? null };
  }

  // The token lives in the API's `squid_csrf` cookie, which only src/server reads, so ask the
  // API once per page. A failed fetch is forgotten so the next write tries again.
  #csrfToken(): Promise<string> {
    this.#csrf ??= browserCsrfGet({ client: this.client })
      .then((result) => unwrap(result).csrf_token)
      .catch((error: unknown) => {
        this.#csrf = undefined;
        throw ApiError.from(error);
      });
    return this.#csrf;
  }
}

// `null` shows the problem's own detail: the API localizes it through Accept-Language, and for
// these codes it says more than a generic sentence could. Failures that would leak internals or
// that the visitor cannot act on share the generic message, shown with the error reference.
const errorMessages = {
  ACCOUNT_ALREADY_LINKED: null,
  ACCOUNT_IDENTITY_NOT_FOUND: m.errorGone,
  ACCOUNT_NOT_FOUND: m.errorGone,
  ALIAS_ALREADY_ADDED: null,
  ALIAS_ALREADY_CLAIMED: null,
  ALIAS_IN_USE: null,
  BUILD_BUSY: m.errorBusy,
  BUILD_NOT_FOUND: m.errorGone,
  BUILD_REVISION_MISMATCH: m.errorConflict,
  BUILD_REVISION_REQUIRED: m.errorConflict,
  CLAIM_NOT_FOUND: m.errorGone,
  CREATOR_ALIAS_NOT_FOUND: m.errorGone,
  CREATOR_NOT_FOUND: m.errorGone,
  CONFIGURATION_ERROR: m.errorGeneric,
  CONSENT_REQUIRED: m.errorConsent,
  CONSENT_VERSION_STALE: m.errorConsent,
  DATA_INTEGRITY_ERROR: m.errorGeneric,
  DOMAIN_ERROR: null,
  INFRASTRUCTURE_ERROR: m.errorUnavailable,
  IDEMPOTENCY_CONFLICT: m.errorGeneric,
  IDEMPOTENCY_IN_PROGRESS: m.errorBusy,
  INTERNAL_ERROR: m.errorGeneric,
  INVALID_BUILD: null,
  INVALID_MESSAGE: null,
  INVALID_QUERY: null,
  INVALID_REQUEST: m.errorInvalid,
  INVALID_STATE: null,
  INVALID_ACCOUNT: null,
  INVALID_MERGE_CODE: null,
  INVALID_PROFILE: null,
  INVALID_VERIFICATION_CODE: null,
  INVALID_VERSION: null,
  LAST_IDENTITY: null,
  LINK_RESERVATION_EXPIRED: null,
  INVALID_VOTE_CONFIGURATION: null,
  MESSAGE_NOT_FOUND: m.errorGone,
  MINECRAFT_ACCOUNT_NOT_FOUND: null,
  MINECRAFT_SERVICE_UNAVAILABLE: m.errorUnavailable,
  NOT_FOUND: m.errorGone,
  PAGE_ANCHOR_NOT_FOUND: null,
  PERSISTENCE_ERROR: m.errorGeneric,
  RATE_LIMITED: m.errorRateLimited,
  RECORD_NOT_FOUND: m.errorGone,
  RESTRICTION_NOT_FOUND: m.errorGone,
  SCHEMATIC_INVALID: null,
  SCHEMATIC_NOT_FOUND: m.errorGone,
  SCHEMATIC_RENDER_REFUSED: null,
  SCHEMATIC_RENDER_UNAVAILABLE: m.errorUnavailable,
  SCHEMATIC_SUPPORT_UNAVAILABLE: m.errorUnavailable,
  SCHEMATIC_TIMEOUT: null,
  SCHEMATIC_TOO_LARGE: null,
  SCHEMATIC_WORKER_CRASHED: m.errorGeneric,
  TAG_NOT_FOUND: m.errorGone,
  UNAUTHORIZED: m.errorSignedOut,
  VALIDATION_ERROR: null,
  VERIFICATION_ATTEMPTS_EXHAUSTED: null,
  VERSION_CATALOG_UNAVAILABLE: m.errorUnavailable,
  VOTE_SESSION_NOT_FOUND: m.errorGone,
} satisfies Record<ErrorCode, (() => string) | null>;

/**
 * The sentence to show beside a failed action. Conflicts and lost connections are recognized
 * by status, since they have no code yet; codes this build does not know fall back to the
 * problem's detail, then to the generic message.
 */
export function errorMessage(error: ApiError): string {
  if (error.offline) return m.errorOffline();
  if (error.conflict) return m.errorConflict();
  const code = error.problem?.code;
  const message =
    code != null && Object.hasOwn(errorMessages, code) ? errorMessages[code as ErrorCode] : null;
  if (message !== null) return message();
  return error.problem?.detail ?? m.errorGeneric();
}
