// Apply a display preference in place and save it in the background; see AppearanceSwitch.astro.
import { THEME_COLORS, parseContrast, parseTheme } from "@/ui/themes";

const parsers = { theme: parseTheme, contrast: parseContrast };

function isPreference(name: string): name is keyof typeof parsers {
  return Object.hasOwn(parsers, name);
}

function show(name: keyof typeof parsers, value: string): void {
  const pinned = parsers[name](value);
  const root = document.documentElement;
  if (pinned === undefined) root.removeAttribute(`data-${name}`);
  else root.setAttribute(`data-${name}`, pinned);
  if (name === "theme") {
    const theme = parseTheme(pinned);
    document
      .querySelector('meta[name="color-scheme"]')
      ?.setAttribute("content", theme ?? "light dark");
    for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
      meta.content = THEME_COLORS[theme ?? parseTheme(meta.dataset.scheme) ?? "light"];
    }
  }
  for (const button of document.querySelectorAll<HTMLButtonElement>(
    `[data-appearance-switch] button[name="${name}"]`,
  )) {
    button.setAttribute("aria-pressed", String(button.value === (pinned ?? "system")));
  }
}

for (const form of document.querySelectorAll<HTMLFormElement>("form[data-appearance-switch]")) {
  form.addEventListener("submit", (event) => {
    const button = event.submitter;
    if (!(button instanceof HTMLButtonElement) || !isPreference(button.name)) return;
    event.preventDefault();
    const body = new FormData(form, button);
    show(button.name, button.value);
    // The page already shows the choice; the post only saves it. If saving fails, the
    // choice still holds until the next page load.
    fetch(form.action, { method: "POST", body, redirect: "manual" }).catch(() => undefined);
  });
}
