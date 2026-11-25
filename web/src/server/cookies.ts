import type { APIContext } from "astro";

type Cookies = Pick<APIContext, "cookies">;

/**
 * Display preferences, each stored in a cookie of the same name. No cookie means the page
 * follows the device. Values are raw strings; parse them with `parseTheme` or `parseContrast`.
 */
type Preference = "theme" | "contrast";

export function readPreference({ cookies }: Cookies, name: Preference): string | undefined {
  return cookies.get(name)?.value;
}

/** Store `value`, or delete the cookie for `undefined` so the page follows the device again. */
export function writePreference(
  { cookies }: Cookies,
  name: Preference,
  value: string | undefined,
): void {
  if (value === undefined) {
    cookies.delete(name, { path: "/" });
    return;
  }
  cookies.set(name, value, {
    path: "/",
    // Browsers cap cookie lifetimes at 400 days.
    maxAge: 400 * 24 * 60 * 60,
    sameSite: "lax",
    httpOnly: true,
    // Not `secure`: it holds a display preference, and the test server is plain HTTP.
  });
}

/**
 * The cookies the API sets at sign-in: the session and the token that browser writes echo in
 * a `CSRF-Token` header. The site forwards them when it calls the API for the visitor and
 * never sets them itself, but clears them on sign-out, since the API's own clearing headers
 * reach the server rather than the browser.
 */
const API_SESSION = "__Host-squid_session";
const API_CSRF = "squid_csrf";

/** The visitor's API cookies as a `Cookie` header, or undefined when they have no session. */
export function apiSessionCookie({ cookies }: Cookies): string | undefined {
  const session = cookies.get(API_SESSION)?.value;
  if (session === undefined) return undefined;
  const csrf = cookies.get(API_CSRF)?.value;
  return csrf === undefined
    ? `${API_SESSION}=${session}`
    : `${API_SESSION}=${session}; ${API_CSRF}=${csrf}`;
}

export function apiCsrfToken({ cookies }: Cookies): string | undefined {
  return cookies.get(API_CSRF)?.value;
}

export function clearApiSession({ cookies }: Cookies): void {
  cookies.delete(API_SESSION, { path: "/", secure: true, httpOnly: true, sameSite: "lax" });
  cookies.delete(API_CSRF, { path: "/", secure: true, sameSite: "lax" });
}
