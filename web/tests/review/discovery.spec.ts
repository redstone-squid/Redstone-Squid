import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { test, expect, type Page } from "@playwright/test";
import { installMediaRoutes, expectCatalogueMetadata } from "../e2e/support";
import type { BundleManifest } from "../../scripts/bundle-budgets";

const manifest = JSON.parse(
  readFileSync("dist/client/.vite/manifest.json", "utf8"),
) as BundleManifest;
test.use({ video: "on" });
async function installDiscoveryMedia(page: Page): Promise<void> {
  await installMediaRoutes(page);
  await page.route("https://media.fixture.invalid/build-1.png", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: readFileSync("public/fixtures/copper-door.svg"),
    }),
  );
}
test.beforeEach(async ({ page }) => installDiscoveryMedia(page));
for (const locale of ["en", "zh-CN"]) {
  const prefix = locale === "en" ? "" : "/zh-cn";
  for (const [name, path] of [
    ["home", "/"],
    ["builds", "/builds"],
    ["results", "/search?q=door"],
    ["error", "/search?q=syntax%3Aerror"],
    ["empty", "/builds?q=no-matches"],
    ["unavailable", "/builds?q=force%3Aunavailable"],
  ] as const) {
    test(`discovery / ${locale} / ${name}`, async ({ page }, info) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      const route = prefix && path === "/" ? prefix : `${prefix}${path}`;
      const response = await page.goto(route);
      expect(response?.status()).toBe(name === "error" ? 400 : name === "unavailable" ? 503 : 200);
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      await expectCatalogueMetadata(page, route);
      await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all(
          Array.from(document.images, (image) => image.decode().catch(() => undefined)),
        );
      });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      expect(
        (
          await new AxeBuilder({ page })
            .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
            .analyze()
        ).violations,
      ).toEqual([]);
      await expect(page.locator("astro-island")).toHaveCount(name === "home" ? 0 : 1);
      const reactRenderer = manifest["node_modules/@astrojs/react/dist/client.js"]?.file;
      if (!reactRenderer) throw new Error("React renderer missing from build manifest");
      const resources = await page.evaluate(() =>
        performance.getEntriesByType("resource").map((resource) => new URL(resource.name).pathname),
      );
      expect(resources).not.toContain(`/${reactRenderer}`);
      expect(errors).toEqual([]);
      if (["desktop", "mobile"].includes(info.project.name))
        await expect(page).toHaveScreenshot(`discovery-${name}-${locale}.png`, { fullPage: true });
      const screenshot = info.outputPath("discovery.png");
      await page.screenshot({ path: screenshot, fullPage: true });
      await info.attach(`${name}-${locale}`, { path: screenshot, contentType: "image/png" });
    });
  }
  test(`discovery / ${locale} / search workflow`, async ({ page }, info) => {
    await page.goto(`${prefix}/builds`);
    await page.getByLabel(locale === "en" ? "Keywords" : "关键词", { exact: true }).fill("do");
    const input = page.getByLabel(locale === "en" ? "Keywords" : "关键词", { exact: true });
    const suggestion = page.getByRole("option", { name: "door", exact: true });
    await expect(suggestion).toBeVisible();
    if (info.project.name === "mobile") await suggestion.tap();
    else await suggestion.click();
    await expect(input).toHaveValue("door");
    await input.fill("do");
    await expect(suggestion).toBeVisible();
    await input.press("ArrowDown");
    await input.press("Escape");
    await expect(page.getByRole("listbox")).toHaveCount(0);
    await input.fill("");
    await page
      .getByLabel(locale === "en" ? "Creator" : "作者", { exact: true })
      .fill("Space Builder");
    await page
      .getByLabel(locale === "en" ? "Category" : "类别", { exact: true })
      .selectOption("entrance");
    await page.getByText(locale === "en" ? "More filters" : "更多筛选", { exact: true }).click();
    await page
      .getByLabel(locale === "en" ? "Maximum width" : "最大宽度", { exact: true })
      .fill("12");
    const screenshot = info.outputPath("filters.png");
    await page.screenshot({ path: screenshot, fullPage: true });
    await info.attach("expanded filters", { path: screenshot, contentType: "image/png" });
    await page
      .getByRole("button", { name: locale === "en" ? "Search" : "搜索", exact: true })
      .click();
    await expect(page.getByRole("link", { name: "Observerless Iris" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Copper Bolt 5×5" })).toHaveCount(0);
    expect(new URL(page.url()).searchParams.get("mode")).toBe("guided");
  });
}

test("discovery / edits existing guided filters and switches mode without JavaScript", async ({
  browser,
}) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  try {
    await installDiscoveryMedia(page);
    await page.goto("http://127.0.0.1:4321/builds?mode=guided&q=old&creator=CircuitSage");
    await page.getByLabel("Creator", { exact: true }).fill("Space Builder");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.getByRole("link", { name: "Observerless Iris" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Copper Bolt 5×5" })).toHaveCount(0);
    await page.getByRole("link", { name: "Advanced", exact: true }).click();
    await page.getByLabel("Advanced query").fill("door");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.getByRole("link", { name: "Copper Bolt 5×5" })).toBeVisible();
    await page.getByRole("link", { name: "Load more" }).click();
    await expect(page.getByRole("link", { name: "Slim Piston Extender" })).toBeVisible();
  } finally {
    await context.close();
  }
});

for (const path of ["/builds?q=door&after_id=2", "/builds/1"]) {
  test(`language dropdown without JavaScript / ${path}`, async ({ browser }, info) => {
    const context = await browser.newContext({
      javaScriptEnabled: false,
      viewport: info.project.use.viewport,
      isMobile: info.project.use.isMobile,
      hasTouch: info.project.use.hasTouch,
    });
    try {
      const page = await context.newPage();
      await installDiscoveryMedia(page);
      await page.goto(`http://127.0.0.1:4321${path}`);
      const selector = page.locator(".language-selector summary");
      await selector.focus();
      await page.keyboard.press("Enter");
      await expect(page.getByRole("link", { name: "English", exact: true })).toHaveAttribute(
        "aria-current",
        "true",
      );
      const chinese = page.getByRole("link", { name: "简体中文", exact: true });
      await expect(chinese).toHaveAttribute("href", `/zh-cn${path}`);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      const screenshot = info.outputPath("languages.png");
      await page.screenshot({ path: screenshot });
      await info.attach("language dropdown", { path: screenshot, contentType: "image/png" });
      await chinese.click();
      await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
      await page.locator(".language-selector summary").click();
      await page.getByRole("link", { name: "English", exact: true }).click();
      expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe(path);
    } finally {
      await context.close();
    }
  });
}
