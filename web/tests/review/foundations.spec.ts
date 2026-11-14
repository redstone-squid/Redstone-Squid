import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "./test";

for (const locale of ["en", "zh-CN"]) {
  for (const theme of ["light", "dark"]) {
    test(`${locale} / ${theme} / foundations`, async ({ page }, testInfo) => {
      await page.goto(`/design-system?lang=${locale}&theme=${theme}`);
      await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all(Array.from(document.images, (image) => image.decode()));
      });
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.locator('meta[name="robots"]')).toHaveAttribute(
        "content",
        "noindex,nofollow",
      );
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      const name = `${locale}-${theme}`;
      const screenshot = testInfo.outputPath(`${name}.png`);
      await page.screenshot({ path: screenshot, fullPage: true, animations: "disabled" });
      await testInfo.attach(name, { path: screenshot, contentType: "image/png" });
      await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true });
    });
  }
}

test("public workbench content renders without JavaScript", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto("http://127.0.0.1:4321/design-system");
    await expect(page.getByRole("heading", { name: "Built to build on." })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Copper Bolt 5×5" })).toBeVisible();
    await page.getByRole("link", { name: "中文", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  } finally {
    await context.close();
  }
});

test("reflows at 320px and retains controls in forced colours", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/design-system?lang=zh-CN");
  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "跳至内容" });
  await expect(skipLink).toBeFocused();
  await expect(skipLink).toHaveCSS("clip-path", "none");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await expect(page.getByRole("button", { name: "新建草稿" })).toBeVisible();
  await page.getByRole("button", { name: "新建草稿" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
});
