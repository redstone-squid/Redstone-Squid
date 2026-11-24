import type { APIContext } from "astro";

type Cookies = Pick<APIContext, "cookies">;

/** The visitor's pinned theme name. No cookie means the page follows the device. */
const THEME_COOKIE = "theme";

/** The theme cookie's raw value; parse it with `parseTheme`. */
export function readThemeCookie({ cookies }: Cookies): string | undefined {
  return cookies.get(THEME_COOKIE)?.value;
}

/** Pin `theme`, or delete the cookie for `undefined` so the page follows the device again. */
export function writeThemeCookie({ cookies }: Cookies, theme: string | undefined): void {
  if (theme === undefined) {
    cookies.delete(THEME_COOKIE, { path: "/" });
    return;
  }
  cookies.set(THEME_COOKIE, theme, {
    path: "/",
    // Browsers cap cookie lifetimes at 400 days.
    maxAge: 400 * 24 * 60 * 60,
    sameSite: "lax",
    httpOnly: true,
    // Not `secure`: it holds a display preference, and the test server is plain HTTP.
  });
}
