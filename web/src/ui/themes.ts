/** Themes defined in themes.css. The layout writes the chosen one to <html data-theme>. */
export const THEMES = ["light", "dark"] as const;
export type Theme = (typeof THEMES)[number];
const DEFAULT_THEME: Theme = "light";

/** Read a theme name, such as a query parameter; unknown or missing names give the default. */
export function parseTheme(value: string | null): Theme {
  return THEMES.find((theme) => theme === value) ?? DEFAULT_THEME;
}
