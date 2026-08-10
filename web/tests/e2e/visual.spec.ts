import { expect, test } from "@playwright/test";

import { installMediaRoutes } from "./support";

const pages = [
  ["home", "/"],
  ["builds", "/builds?q=door"],
  ["build-detail", "/builds/1"],
  ["record-detail", "/records/11"],
  ["search-error", "/search?q=syntax%3Aerror"],
  ["chinese", "/zh-cn/builds"],
] as const;

for (const [name, path] of pages) {
  test(`visual baseline: ${name}`, async ({ page }, testInfo) => {
    test.skip(
      !["chromium-desktop", "mobile-chrome"].includes(testInfo.project.name),
      "Visual goldens are intentionally limited to stable Chromium desktop and mobile.",
    );
    await installMediaRoutes(page);
    await page.goto(path);
    await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true });
  });
}
