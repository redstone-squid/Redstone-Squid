import { expect, test } from "@tests/support/test";

// reflow.spec.ts checks that pseudo text fits at every width; this checks that it is served.
for (const path of ["/en-xa/design-system", "/en-xa/design-system-trial"]) {
  test(`${path} renders pseudo text`, async ({ page }) => {
    await page.goto(path);
    await expect(page.locator("html")).toHaveAttribute("lang", "en-XA");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(/^\[.*\]$/);
  });
}
