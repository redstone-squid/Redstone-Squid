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
