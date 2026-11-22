import type { APIRoute } from "astro";

export const GET: APIRoute = ({ locals }) => {
  const { config } = locals;
  return new Response(
    `User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: ${config.siteUrl}/sitemap.xml\n`,
    {
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "public, max-age=3600",
      },
    },
  );
};
