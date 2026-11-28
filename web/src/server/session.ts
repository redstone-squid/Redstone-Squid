import type { APIContext } from "astro";
import { ApiError, unwrap } from "@/api-client/api-error";
import { browserSessionGet, browserSessionRevoke } from "@/generated/sdk.gen";
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
    const error = ApiError.from(caught);
    if (error.status !== 401) {
      context.locals.sessionError = error;
      logFailure("session_load_failed", error);
    }
    return null;
  }
}

/** Get the cookie for a protected page or let middleware handle the failure. */
export function requireSession(context: Context): string {
  context.locals.requiresSession = true;
  if (context.locals.sessionError) throw context.locals.sessionError;
  const cookie = apiSessionCookie(context);
  if (cookie === undefined || !context.locals.session?.user) throw new ApiError(401);
  return cookie;
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

export function writeHeaders(context: Context): Record<string, string> | undefined {
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
