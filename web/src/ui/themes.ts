/** Themes defined in themes.css. The layout writes the chosen one to <html data-theme>. */
export const THEMES = ["light", "dark"] as const;
export type Theme = (typeof THEMES)[number];
const DEFAULT_THEME: Theme = "light";

/**
 * Each theme's `--canvas` as hex, for `<meta name="theme-color">` and the web manifest, which
 * cannot read CSS variables. Keep in step with themes.css.
 */
export const THEME_COLORS = { light: "#f5f5f4", dark: "#151b18" } as const satisfies Record<
  Theme,
  string
>;

/** Read a theme name, such as a query parameter; unknown or missing names give the default. */
export function parseTheme(value: string | null): Theme {
  return THEMES.find((theme) => theme === value) ?? DEFAULT_THEME;
}
