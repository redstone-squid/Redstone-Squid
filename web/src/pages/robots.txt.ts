import type { APIRoute } from "astro";

import { getRuntimeConfig } from "../lib/config";

export const GET: APIRoute = () => {
  const config = getRuntimeConfig();
  return new Response(
    `User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /zh-cn/api/\nSitemap: ${config.siteUrl}/sitemap.xml\n`,
    {
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "public, max-age=3600",
      },
    },
  );
};
