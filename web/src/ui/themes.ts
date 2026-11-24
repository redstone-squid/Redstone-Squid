/**
 * The color schemes in themes.css. `<html data-theme>` pins one; without it the page follows
 * the device, so code uses `undefined` for "follow the device".
 */
const THEMES = ["light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

/**
 * Each theme's `--canvas` as hex, for `<meta name="theme-color">` and the web manifest, which
 * cannot read CSS variables. Keep in step with themes.css.
 */
export const THEME_COLORS = { light: "#f5f5f4", dark: "#151b18" } as const satisfies Record<
  Theme,
  string
>;

/** Read a theme name, such as a cookie or query value; unknown or missing names give undefined. */
export function parseTheme(value: string | null | undefined): Theme | undefined {
  return THEMES.find((theme) => theme === value);
}
