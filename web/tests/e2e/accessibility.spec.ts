import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { installMediaRoutes } from "./support";

const layouts = ["/", "/builds", "/builds/1", "/records/11", "/search?q=door", "/zh-cn/about"];

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
