import type { APIRoute } from "astro";

import { fetchBuilds, fetchRecords } from "../lib/api";
import { getRuntimeConfig } from "../lib/config";
import { localizePath, type Locale } from "../lib/i18n";

const CACHE_MS = 60 * 60 * 1_000;
let cached: { expiresAt: number; xml: string } | undefined;

function escapeXml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function urlEntry(siteUrl: string, path: string, lastModified?: string): string {
  const alternates = (["en", "zh-CN"] as const)
    .map(
      (locale) =>
        `<xhtml:link rel="alternate" hreflang="${locale}" href="${escapeXml(new URL(localizePath(path, locale), siteUrl).toString())}"/>`,
    )
    .join("");
  return `<url><loc>${escapeXml(new URL(path, siteUrl).toString())}</loc>${alternates}${lastModified ? `<lastmod>${escapeXml(lastModified)}</lastmod>` : ""}</url>`;
}

async function cataloguePaths(locale: Locale): Promise<{ path: string; modified?: string }[]> {
  const paths: { path: string; modified?: string }[] = [
    { path: "/" },
    { path: "/builds" },
    { path: "/records" },
    { path: "/search" },
    { path: "/search/help" },
    { path: "/about" },
  ];
  let buildCursor: string | undefined;
  do {
    const page = await fetchBuilds(locale, { cursor: buildCursor, pageSize: 50 });
    paths.push(
      ...page.items.map((build) => ({
        path: `/builds/${build.id}`,
        ...(build.updated_at ? { modified: build.updated_at } : {}),
      })),
    );
    buildCursor = page.next_cursor ?? undefined;
  } while (buildCursor);

  let recordCursor: string | undefined;
  do {
    const page = await fetchRecords(locale, recordCursor, 50);
    paths.push(
      ...page.items.map((record) => ({
        path: `/records/${record.id}`,
        modified: record.computed_at,
      })),
    );
    recordCursor = page.next_cursor ?? undefined;
  } while (recordCursor);
  return paths;
}

export const GET: APIRoute = async () => {
  if (cached && cached.expiresAt > Date.now()) {
    return new Response(cached.xml, {
      headers: {
        "Content-Type": "application/xml; charset=utf-8",
        "Cache-Control": "public, max-age=3600",
      },
    });
  }
  try {
    const config = getRuntimeConfig();
    const paths = await cataloguePaths("en");
    const entries = paths.flatMap(({ path, modified }) =>
      (["en", "zh-CN"] as const).map((locale) =>
        urlEntry(config.siteUrl, localizePath(path, locale), modified),
      ),
    );
    const xml = `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">${entries.join("")}</urlset>`;
    cached = { xml, expiresAt: Date.now() + CACHE_MS };
    return new Response(xml, {
      headers: {
        "Content-Type": "application/xml; charset=utf-8",
        "Cache-Control": "public, max-age=3600",
      },
    });
  } catch (error) {
    console.error(
      JSON.stringify({
        event: "sitemap_generation_failed",
        error: error instanceof Error ? error.message : String(error),
      }),
    );
    return new Response("Sitemap temporarily unavailable", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
};
