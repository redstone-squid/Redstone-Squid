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
