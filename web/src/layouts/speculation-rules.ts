/**
 * Fetch a same-origin page when the visitor hovers or presses its link, so it opens at once.
 * Prefetch rather than prerender: a prerendered page would run its scripts and count a visit.
 * `astro.config.ts` hashes this exact text into the content security policy.
 */
export const SPECULATION_RULES = JSON.stringify({
  prefetch: [
    {
      where: {
        and: [
          { href_matches: "/*" },
          { not: { href_matches: "/api/*" } },
          { not: { href_matches: "/telemetry/*" } },
          { not: { selector_matches: "[rel~=nofollow], [download]" } },
        ],
      },
      eagerness: "moderate",
    },
  ],
});
