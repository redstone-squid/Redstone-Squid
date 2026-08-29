import type { APIRoute } from "astro";

import { respondWithSuggestions } from "../../../../lib/suggest-route";

export const GET: APIRoute = ({ params, url }) => respondWithSuggestions("zh-CN", params.source, url);
