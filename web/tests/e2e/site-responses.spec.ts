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
