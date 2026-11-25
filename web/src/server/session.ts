import type { APIContext } from "astro";
import { ApiError, unwrap } from "@/api-client/api-error";
import { accountConsentGrant, browserSessionGet, browserSessionRevoke } from "@/generated/sdk.gen";
import type { SessionSnapshot } from "@/generated/types.gen";
import { localizePath } from "@/locales/locale";
import { apiCsrfToken, apiSessionCookie, clearApiSession } from "./cookies";

type Context = Pick<APIContext, "cookies" | "locals">;

/**
 * The visitor's session, or null when they sent no session cookie. An expired cookie comes
 * back as a snapshot with a null user. A failed call is logged and also counts as signed out,
 * so the header never takes a page down.
 */
export async function loadSession(context: Context): Promise<SessionSnapshot | null> {
  const cookie = apiSessionCookie(context);
  if (cookie === undefined) return null;
  try {
    return unwrap(
      await browserSessionGet({ client: context.locals.api, headers: { Cookie: cookie } }),
    );
  } catch (caught) {
    logFailure("session_load_failed", ApiError.from(caught));
    return null;
  }
}

type ConsentOutcome = "granted" | "stale" | "signed-out" | "failed";

/**
 * Accept the privacy notice `version` for the visitor, forwarding their session.
 *
 * The site's own form posts here, so the API's double-submit CSRF check is satisfied by copying
 * the visitor's token from their cookie into the header. That is safe only because Astro's
 * `checkOrigin` rejects cross-site form posts before this runs. "stale" means a newer notice was
 * published after the visitor loaded the page.
 */
export async function grantConsent(context: Context, version: string): Promise<ConsentOutcome> {
  const headers = writeHeaders(context);
  if (headers === undefined) return "signed-out";
  try {
    unwrap(await accountConsentGrant({ client: context.locals.api, headers, body: { version } }));
    return "granted";
  } catch (caught) {
    const error = ApiError.from(caught);
    if (error.problem?.code === "CONSENT_VERSION_STALE") return "stale";
    if (error.status === 401) return "signed-out";
    logFailure("consent_grant_failed", error);
    return "failed";
  }
}

/**
 * Revoke the visitor's session and clear its cookies. The cookies are cleared even when the API
 * call fails, so the browser is signed out either way; an unrevoked session expires on its own.
 */
export async function signOut(context: Context): Promise<void> {
  const headers = writeHeaders(context);
  if (headers !== undefined) {
    try {
      unwrap(await browserSessionRevoke({ client: context.locals.api, headers }));
    } catch (caught) {
      const error = ApiError.from(caught);
      if (error.status !== 401) logFailure("sign_out_failed", error);
    }
  }
  clearApiSession(context);
}

/**
 * A `redirect_to` or `return` value as a path on this site, or the localized home page.
 * Anything that resolves to another origin, such as `//evil.example` or `/\evil.example`,
 * is refused so sign-in cannot become an open redirect. It is parsed as a browser
 * would, which also drops the tabs and newlines browsers ignore.
 */
export function returnPath(value: FormDataEntryValue | null | undefined): string {
  const base = "https://catalogue.invalid";
  if (typeof value !== "string" || !value.startsWith("/")) return localizePath("/");
  const url = new URL(value, base);
  return url.origin === base ? `${url.pathname}${url.search}${url.hash}` : localizePath("/");
}

function writeHeaders(context: Context): Record<string, string> | undefined {
  const cookie = apiSessionCookie(context);
  const csrf = apiCsrfToken(context);
  if (cookie === undefined || csrf === undefined) return undefined;
  return { Cookie: cookie, "CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() };
}

function logFailure(event: string, error: ApiError): void {
  console.error(
    JSON.stringify({
      event,
      status: error.status,
      request_id: error.requestId,
      error: error.message,
    }),
  );
}
