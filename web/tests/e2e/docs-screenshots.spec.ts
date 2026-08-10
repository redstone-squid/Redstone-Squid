import path from "node:path";

import { test } from "@playwright/test";

import { installMediaRoutes } from "./support";

test("captures curated documentation layouts", async ({ page }, testInfo) => {
  test.skip(process.env.DOCS_SCREENSHOTS !== "1", "On-demand documentation capture only.");
  const desktop = testInfo.project.name === "chromium-desktop";
  const mobile = testInfo.project.name === "mobile-chrome";
  test.skip(
    !desktop && !mobile,
    "Only the curated English desktop and Chinese mobile projects capture docs.",
  );
  await installMediaRoutes(page);
  const localePrefix = desktop ? "" : "/zh-cn";
  const language = desktop ? "en-desktop" : "zh-cn-mobile";
  const pages = [
    ["home", `${localePrefix}/`],
    ["builds", `${localePrefix}/builds?q=door`],
    ["build-detail", `${localePrefix}/builds/1`],
    ["record-detail", `${localePrefix}/records/11`],
  ] as const;
  for (const [name, pagePath] of pages) {
    await page.goto(pagePath);
    await page.screenshot({
      path: path.resolve(process.cwd(), "../catalogue-screenshots", `${language}-${name}.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
});
