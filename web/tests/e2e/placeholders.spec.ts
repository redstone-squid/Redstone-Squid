import { expect, test } from "../support/test";

const routes = [
  "/",
  "/about",
  "/admin/settings",
  "/builds",
  "/builds/1",
  "/cli/link?user_code=example",
  "/creators/Space%20Builder",
  "/minecraft/link?user_code=example",
  "/records",
  "/records/11",
  "/search?q=door",
  "/search/help",
  "/submit",
];

for (const [prefix, locale, message] of [
  ["", "en", "The Redstone Squid website is being rebuilt."],
  ["/zh-cn", "zh-CN", "红石鱿鱼网站正在重建。"],
] as const) {
  test(`catalogue routes show placeholders in ${locale}`, async ({ page }) => {
    for (const route of routes) {
      const response = await page.goto(`${prefix}${route}`);
      expect(response?.status(), route).toBe(200);
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      await expect(page.getByText(message, { exact: true })).toBeVisible();
      await expect(page.locator("astro-island")).toHaveCount(0);
      const path = new URL(route, "https://catalogue.invalid").pathname;
      await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
        "href",
        `https://catalogue.redstone-squid.org${prefix}${path === "/" && prefix !== "" ? "" : path}`,
      );
      await expect(page.locator('meta[name="robots"]')).toHaveAttribute(
        "content",
        route === "/" ? "index,follow" : "noindex,follow",
      );
    }
  });
}

test("the language selector preserves a detail URL and its query", async ({ page }) => {
  await page.goto("/builds/1?view=compact");
  await page.locator(".language-selector summary").click();
  await page.getByRole("link", { name: "简体中文" }).click();
  await expect(page).toHaveURL("/zh-cn/builds/1?view=compact");
});

test("status pages and sitemap stay available during the rebuild", async ({ request }) => {
  for (const [route, status] of [
    ["/unknown", 404],
    ["/zh-cn/unknown", 404],
    ["/503", 503],
    ["/zh-cn/503", 503],
  ] as const) {
    expect((await request.get(route)).status(), route).toBe(status);
  }

  const response = await request.get("/sitemap.xml");
  expect(response.ok()).toBe(true);
  const xml = await response.text();
  expect(xml.match(/<loc>[^<]*<\/loc>/g)).toEqual([
    "<loc>https://catalogue.redstone-squid.org/</loc>",
    "<loc>https://catalogue.redstone-squid.org/zh-cn</loc>",
  ]);
});

test("the pseudo-locale renders bracketed text and stays out of search and menus", async ({
  page,
}) => {
  await page.goto("/en-xa/records");
  await expect(page.locator("html")).toHaveAttribute("lang", "en-XA");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(/^\[.*\]$/);
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex,follow");
  await expect(page.locator('link[hreflang="en-XA"]')).toHaveCount(0);
  await page.locator(".language-selector summary").click();
  await expect(page.locator(".language-selector a")).toHaveText(["English", "简体中文"]);
});
