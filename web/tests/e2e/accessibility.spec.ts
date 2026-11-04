import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { installMediaRoutes } from "./support";

const layouts = ["/", "/builds", "/builds/1", "/records/11", "/search?q=door", "/zh-cn/about"];

// Interactive islands render client-side. Wait for a control or error alert before
// scanning their initial surface; this does not exercise every later interaction state.
const islands = ["/submit", "/cli/link", "/minecraft/link"];

for (const path of layouts) {
  test(`meets automated WCAG 2.2 AA checks at ${path}`, async ({ page }) => {
    await installMediaRoutes(page);
    await page.goto(path);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  });
}

for (const path of islands) {
  test(`meets automated WCAG 2.2 AA checks at ${path}`, async ({ page }) => {
    await installMediaRoutes(page);
    await page.goto(path);
    await expect(
      page.locator("main").locator('input, select, button, [role="alert"]').first(),
    ).toBeVisible();
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  });
}
