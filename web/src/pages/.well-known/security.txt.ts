import type { APIRoute } from "astro";

const REPOSITORY = "https://github.com/redstone-squid/Redstone-Squid";

/**
 * Tells security researchers where to report a vulnerability (RFC 9116). The file must expire
 * within a year; the contact is a GitHub form that does not go stale, so the date rolls forward
 * rather than becoming a chore nobody remembers.
 */
export const GET: APIRoute = ({ locals }) => {
  const expires = new Date(Date.now() + 180 * 24 * 60 * 60 * 1000);
  expires.setUTCHours(0, 0, 0, 0);
  return new Response(
    [
      `Contact: ${REPOSITORY}/security/advisories/new`,
      `Policy: ${REPOSITORY}/security/policy`,
      `Expires: ${expires.toISOString()}`,
      "Preferred-Languages: en, zh",
      `Canonical: ${locals.config.siteUrl}/.well-known/security.txt`,
      "",
    ].join("\n"),
    {
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "public, max-age=86400",
      },
    },
  );
};
