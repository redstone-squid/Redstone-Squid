import { MAX_SUGGESTION_QUERY_LENGTH } from "../../lib/suggestion-limits";
import { localizePath, type Locale } from "../../lib/i18n";
import type { SuggestionPage } from "../../generated/types.gen";

/** Keep public suggestions on the locale-aware, same-origin proxy. */
export async function loadSuggestions(
  locale: Locale,
  source: string,
  value: string,
  cursor: number | null,
  signal: AbortSignal,
) {
  // Longer advanced queries can still be submitted; the suggestion API accepts only 200 characters.
  if (value.length > MAX_SUGGESTION_QUERY_LENGTH) return { items: [], replacement: null };
  const parameters = new URLSearchParams({ q: value });
  if (source === "search_query" && cursor !== null) parameters.set("cursor", String(cursor));
  const response = await fetch(
    `${localizePath(`/api/suggest/${encodeURIComponent(source)}`, locale)}?${parameters}`,
    { signal, headers: { Accept: "application/json" } },
  );
  if (!response.ok) throw new Error(`Suggestion endpoint returned ${response.status}`);
  const result = (await response.json()) as SuggestionPage;
  return {
    items: Array.isArray(result.items) ? result.items : [],
    replacement: result.replacement,
  };
}
