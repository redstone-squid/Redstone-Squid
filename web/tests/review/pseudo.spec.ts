import { expect, test } from "@tests/support/test";

// Pseudo text is about 40% longer than English, so fixed widths and clipped labels show up here
// before a real translation runs into them.
for (const path of ["/en-xa/design-system", "/en-xa/design-system-trial"]) {
  test(`${path} fits longer text`, async ({ page }) => {
    await page.goto(path);
    await expect(page.locator("html")).toHaveAttribute("lang", "en-XA");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(/^\[.*\]$/);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });
}
