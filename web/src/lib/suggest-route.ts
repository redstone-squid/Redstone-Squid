import { asCatalogueError, fetchSuggestionPage } from "./api";
import type { Locale } from "./i18n";

const SOURCE_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
const MAX_QUERY_LENGTH = 200;

const UNAVAILABLE_TITLE: Record<Locale, string> = {
  en: "Suggestions unavailable",
  "zh-CN": "无法获取搜索建议",
};

/**
 * Proxy a suggestion source using the locale selected by the route.
 *
 * Validate the source slug, query length, and numeric cursor before contacting the API.
 * Successful pages, including empty results, are privately cacheable for 30 seconds.
 * Upstream failures preserve their status and disable caching.
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
