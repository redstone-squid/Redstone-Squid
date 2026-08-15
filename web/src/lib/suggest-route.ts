import { asCatalogueError, fetchSuggestionPage } from "./api";
import type { Locale } from "./i18n";

const SOURCE_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
const MAX_QUERY_LENGTH = 200;

const UNAVAILABLE_TITLE: Record<Locale, string> = {
  en: "Suggestions unavailable",
  "zh-CN": "无法获取搜索建议",
};

/**
 * Answer one suggestion-source request on behalf of the browser.
 *
 * Shared by the per-locale route files, which exist because this site prefixes localized pages
 * rather than negotiating a locale per request. Only the locale differs between them, so it is
 * the only thing they pass in.
 *
 * An empty result is a 200 with no items rather than an error: a dropdown with nothing in it is
 * the correct answer to a prefix that matches nothing.
 */
export async function respondWithSuggestions(
  locale: Locale,
  source: string | undefined,
  url: URL,
): Promise<Response> {
  if (!source || !SOURCE_PATTERN.test(source)) {
    return Response.json({ title: "Unknown suggestion source", status: 404 }, { status: 404 });
  }
  const q = url.searchParams.get("q") ?? "";
  if (q.length > MAX_QUERY_LENGTH) {
    return Response.json({ title: "Query too long", status: 400 }, { status: 400 });
  }
  const rawCursor = url.searchParams.get("cursor");
  const cursor = rawCursor === null ? undefined : Number(rawCursor);
  if (cursor !== undefined && (!Number.isSafeInteger(cursor) || cursor < 0)) {
    return Response.json({ title: "Invalid cursor", status: 400 }, { status: 400 });
  }

  try {
    const result = await fetchSuggestionPage(locale, { source, q, cursor });
    return Response.json(result, {
      headers: { "Cache-Control": "private, max-age=30", "X-Content-Type-Options": "nosniff" },
    });
  } catch (caught) {
    const error = asCatalogueError(caught);
    return Response.json(
      { title: error.problem?.title ?? UNAVAILABLE_TITLE[locale], status: error.status },
      { status: error.status, headers: { "Cache-Control": "no-store" } },
    );
  }
}
