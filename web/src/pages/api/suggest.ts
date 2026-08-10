import type { APIRoute } from "astro";

import { asCatalogueError, fetchSuggestions } from "../../lib/api";

export const GET: APIRoute = async ({ url }) => {
  const q = url.searchParams.get("q")?.trim() ?? "";
  if (!q || q.length > 1_000) {
    return Response.json({ suggestions: [] }, { status: 400 });
  }
  try {
    const result = await fetchSuggestions("en", q);
    return Response.json(result, {
      headers: { "Cache-Control": "private, max-age=30", "X-Content-Type-Options": "nosniff" },
    });
  } catch (caught) {
    const error = asCatalogueError(caught);
    return Response.json(
      { title: error.problem?.title ?? "Suggestions unavailable", status: error.status },
      { status: error.status, headers: { "Cache-Control": "no-store" } },
    );
  }
};
