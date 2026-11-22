import { expect, test } from "@tests/support/test";

for (const { prefix, locale } of [
  { prefix: "", locale: "en" },
  { prefix: "/zh-cn", locale: "zh-CN" },
]) {
  test.describe(`tag pages (${locale})`, () => {
    test("renders the current tag", async ({ page }) => {
      const response = await page.goto(`${prefix}/tags/zero-tick`);
      expect(response?.status()).toBe(200);
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
      await expect(page.getByRole("heading", { level: 1, name: "Zero tick" })).toBeVisible();
    });

    test("redirects a former slug and preserves the query", async ({ request }) => {
      const response = await request.get(`${prefix}/tags/old-name?q=1`, { maxRedirects: 0 });
      expect(response.status()).toBe(302);
      expect(response.headers()["cache-control"]).toBe("no-store");
      expect(response.headers().location).toBe(`${prefix}/tags/zero-tick?q=1`);
    });

    test("returns 404 for an unknown tag", async ({ request }) => {
      expect((await request.get(`${prefix}/tags/missing`)).status()).toBe(404);
    });
  });
}
