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
