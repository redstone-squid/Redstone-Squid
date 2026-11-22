import type { APIRoute } from "astro";

import { SUPPORTED_LOCALES, localizePath } from "../locales/locale";

export const GET: APIRoute = ({ locals }) => {
  const { siteUrl } = locals.config;
  const homes = SUPPORTED_LOCALES.map((locale) => {
    const url = new URL(localizePath("/", locale), siteUrl).href
      .replaceAll("&", "&amp;")
      .replaceAll('"', "&quot;");
    return { locale, url };
  });
  const alternates = homes
    .map(({ locale, url }) => `<xhtml:link rel="alternate" hreflang="${locale}" href="${url}"/>`)
    .join("");
  const entries = homes.map(({ url }) => `<url><loc>${url}</loc>${alternates}</url>`).join("");
  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">${entries}</urlset>`,
    {
      headers: {
        "Content-Type": "application/xml; charset=utf-8",
        "Cache-Control": "public, max-age=3600",
      },
    },
  );
};
