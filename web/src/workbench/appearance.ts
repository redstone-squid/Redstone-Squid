import { localizePath } from "@/locales/locale";
import { parseContrast, parseTheme } from "@/ui/themes";

export function workbenchAppearance(url: URL, locals: Pick<App.Locals, "theme" | "contrast">) {
  const theme = parseTheme(url.searchParams.get("theme")) ?? locals.theme ?? "light";
  const contrast = parseContrast(url.searchParams.get("contrast"));
  const preview = new URLSearchParams({ theme });
  if (contrast !== undefined) preview.set("contrast", contrast);

  return {
    theme,
    contrast,
    effectiveContrast: contrast ?? locals.contrast,
    href: (route: string) => localizePath(`${route}?${preview}`),
  };
}
