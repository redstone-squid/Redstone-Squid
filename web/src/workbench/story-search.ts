import { readonly, writable } from "svelte/store";
import { parseTheme } from "@/ui/themes";

// These links live in the Astro shell, outside the interactive stories.
function syncLinks(params: URLSearchParams): void {
  const carryQuery = "[data-workbench-language] a, a[data-workbench-media]";
  for (const link of document.querySelectorAll<HTMLAnchorElement>(carryQuery)) {
    const target = new URL(link.href);
    target.search = params.toString();
    link.href = target.href;
  }
  const trial = document.querySelector<HTMLAnchorElement>("[data-workbench-trial]");
  if (trial !== null) {
    const target = new URL(trial.href);
    target.searchParams.set("theme", parseTheme(params.get("theme")));
    trial.href = target.href;
  }
}

const search = writable<URLSearchParams | undefined>(undefined, (set) => {
  if (typeof window === "undefined") return;
  function read(): void {
    const params = new URLSearchParams(location.search);
    syncLinks(params);
    set(params);
  }
  read();
  window.addEventListener("popstate", read);
  return () => window.removeEventListener("popstate", read);
});

/** The live query in the browser. Each story receives its initial query from Astro. */
export const storySearch = readonly(search);

/** Keep the current preview shareable without navigating or adding a history entry per edit. */
export function setStoryOption(parameter: string, value: string | null): void {
  const url = new URL(location.href);
  if (value === null) url.searchParams.delete(parameter);
  else url.searchParams.set(parameter, value);
  history.replaceState(history.state, "", url);
  syncLinks(url.searchParams);
  search.set(url.searchParams);
}
