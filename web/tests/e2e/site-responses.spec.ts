import { z } from "zod";
import * as m from "@/locales/messages.gen";
import { expect, test } from "@tests/support/test";

for (const [route, status] of [
  ["/unknown", 404],
  ["/zh-cn/unknown", 404],
  ["/503", 503],
  ["/zh-cn/503", 503],
] as const) {
  test(`${route} returns ${status}`, async ({ request }) => {
    expect((await request.get(route)).status()).toBe(status);
  });
}

test("the sitemap lists the indexable home pages during the rebuild", async ({ request }) => {
  const response = await request.get("/sitemap.xml");
  expect(response.ok()).toBe(true);
  const xml = await response.text();
  expect(xml.match(/<loc>[^<]*<\/loc>/g)).toEqual([
    "<loc>https://catalogue.redstone-squid.org/</loc>",
    "<loc>https://catalogue.redstone-squid.org/zh-cn</loc>",
  ]);
});

test("pages forbid framing and scripts that Astro did not emit", async ({ request }) => {
  const response = await request.get("/zh-cn/builds");
  const headers = response.headers();
  const policy = headers["content-security-policy"] ?? "";
  expect(policy).toContain("frame-ancestors 'none'");
  expect(policy).toMatch(/script-src 'self'( 'sha256-[^']+')*;/);
  expect(policy).toContain("report-to csp");
  expect(headers["reporting-endpoints"]).toBe('csp="/telemetry/csp"');
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["cross-origin-opener-policy"]).toBe("same-origin");
  expect(headers["permissions-policy"]).toContain("camera=()");
});

test("policy violation reports are accepted and other bodies refused", async ({ request }) => {
  const report = {
    type: "csp-violation",
    age: 0,
    url: "https://catalogue.redstone-squid.org/builds?secret=1",
    body: {
      documentURL: "https://catalogue.redstone-squid.org/builds?secret=1",
      blockedURL: "inline",
      effectiveDirective: "script-src-elem",
      disposition: "enforce",
    },
  };
  const accepted = await request.post("/telemetry/csp", {
    headers: { "Content-Type": "application/reports+json" },
    data: JSON.stringify([report, { type: "deprecation", body: {} }]),
  });
  expect(accepted.status()).toBe(204);
  const wrongType = await request.post("/telemetry/csp", { data: [report] });
  expect(wrongType.status()).toBe(415);
});

test("security.txt names a contact and expires within a year", async ({ request }) => {
  const response = await request.get("/.well-known/security.txt");
  expect(response.ok()).toBe(true);
  const text = await response.text();
  expect(text).toContain(
    "Contact: https://github.com/redstone-squid/Redstone-Squid/security/advisories/new",
  );
  const expires = Date.parse(/^Expires: (.+)$/m.exec(text)?.[1] ?? "");
  expect(expires).toBeGreaterThan(Date.now());
  expect(expires).toBeLessThan(Date.now() + 365 * 24 * 60 * 60 * 1000);
});

test("hovering a link prefetches its page", async ({ page, browserName, isMobile }) => {
  test.skip(browserName !== "chromium", "Only Chromium supports speculation rules.");
  test.skip(isMobile, "Touch screens have no hover, and the navigation collapses into a menu.");
  await page.goto("/about");
  const link = page
    .getByRole("navigation", { name: m.navPrimary("en") })
    .getByRole("link", { name: m.navRecords("en") });
  await link.hover();
  // Moderate eagerness fetches after about 200 ms of hovering, and the page cannot observe it.
  // eslint-disable-next-line playwright/no-wait-for-timeout
  await page.waitForTimeout(1000);
  await link.click();
  await expect(page).toHaveURL("/records");
  // TypeScript's DOM types do not have deliveryType yet, so read it from the entry's JSON.
  const entry: unknown = await page.evaluate((): unknown =>
    performance.getEntriesByType("navigation")[0]?.toJSON(),
  );
  const delivery = z.object({ deliveryType: z.string() }).parse(entry).deliveryType;
  expect(delivery).toBe("navigational-prefetch");
});
