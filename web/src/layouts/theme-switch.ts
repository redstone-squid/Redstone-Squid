// Switch the theme in place and save it in the background; see ThemeSwitch.astro.
import { THEME_COLORS, parseTheme, type Theme } from "@/ui/themes";

function showTheme(theme: Theme | undefined): void {
  const root = document.documentElement;
  if (theme === undefined) delete root.dataset.theme;
  else root.dataset.theme = theme;
  document
    .querySelector('meta[name="color-scheme"]')
    ?.setAttribute("content", theme ?? "light dark");
  for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    meta.content = THEME_COLORS[theme ?? parseTheme(meta.dataset.scheme) ?? "light"];
  }
  for (const button of document.querySelectorAll("[data-theme-switch] button")) {
    if (button instanceof HTMLButtonElement)
      button.setAttribute("aria-pressed", String(button.value === (theme ?? "system")));
  }
}

for (const form of document.querySelectorAll<HTMLFormElement>("form[data-theme-switch]")) {
  form.addEventListener("submit", (event) => {
    if (!(event.submitter instanceof HTMLButtonElement)) return;
    event.preventDefault();
    const body = new FormData(form, event.submitter);
    showTheme(parseTheme(event.submitter.value));
    // The page already shows the choice; the post only saves it. If saving fails, the
    // choice still holds until the next page load.
    fetch(form.action, { method: "POST", body, redirect: "manual" }).catch(() => undefined);
  });
}
