import { expect, test } from "../support/test";

test("the language selector preserves a detail URL and its query", async ({ page }) => {
  await page.goto("/tags/zero-tick?view=compact");
  await page.getByLabel("English · Language").click();
  await page.getByRole("link", { name: "简体中文" }).click();
  await expect(page).toHaveURL("/zh-cn/tags/zero-tick?view=compact");
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
});

test("the pseudo-locale formats interface text and stays out of search and menus", async ({
  page,
}) => {
  await page.goto("/en-xa/tags/zero-tick");
  await expect(page.locator("html")).toHaveAttribute("lang", "en-XA");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex,follow");
  await expect(page.locator('link[hreflang="en-XA"]')).toHaveCount(0);
  await page.getByLabel(/^\[.*\] · \[.*\]$/).click();
  await expect(page.getByRole("list", { name: /^\[.*\]$/ }).getByRole("link")).toHaveText([
    "English",
    "简体中文",
  ]);
});
