import { test, expect } from "../support/test";
import { APP_URL } from "../support/servers";

test.use({ video: "on" });
for (const locale of ["en", "zh-CN"]) {
  const labels =
    locale === "en"
      ? {
          title: "Build title",
          width: "Opening width",
          search: "Find a version",
          version: "Minecraft version",
          upload: "Attach a schematic",
          save: "Save draft",
          retry: "Retry save",
          saved: "Draft saved.",
          category: "Category",
          dialog: "New draft",
          close: "Close dialog",
        }
      : {
          title: "作品标题",
          width: "开口宽度",
          search: "搜索版本",
          version: "Minecraft 版本",
          upload: "附加原理图",
          save: "保存草稿",
          retry: "重试保存",
          saved: "草稿已保存。",
          category: "类别",
          dialog: "新建草稿",
          close: "关闭对话框",
        };

  test(`${locale} / draft workflow`, async ({ page, browserName }, testInfo) => {
    await page.goto(`/design-system-trial?lang=${locale}&scenario=retry`);
    await page.getByRole("button", { name: labels.save, exact: true }).click();
    await expect(page.getByLabel(labels.title, { exact: true })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await page.getByLabel(labels.title, { exact: true }).fill("Copper Bolt");
    await page.getByLabel(labels.width).fill("5");
    await page.getByLabel(labels.search).fill("1.21.4");
    await expect(page.getByRole("option", { name: "Bedrock 1.21.50" })).toHaveCount(0);
    await page.getByLabel(labels.version, { exact: true }).selectOption("Java 1.21.4");
    await page.getByLabel(labels.upload).setInputFiles({
      name: "door.schem",
      mimeType: "application/octet-stream",
      buffer: Buffer.from("fixture"),
    });
    await expect(page.getByRole("progressbar")).toBeVisible();
    await expect(page.getByRole("button", { name: labels.save, exact: true })).toBeDisabled();
    await expect(page.getByText(/door\.schem/)).toBeVisible();
    await page.getByRole("button", { name: labels.save, exact: true }).click();
    await page.getByRole("button", { name: labels.retry, exact: true }).click();
    await expect(page.getByRole("status")).toHaveText(labels.saved);
    await expect(page.locator("astro-island")).toHaveCount(1);
    await expect(page.getByLabel(labels.title, { exact: true })).toHaveValue("Copper Bolt");
    // The review suite runs axe in Chromium; compatibility projects exercise the workflow.
    // Playwright's Firefox 1572 crashes in axe on this form.
    // eslint-disable-next-line playwright/no-conditional-in-test
    if (browserName === "chromium") {
      // eslint-disable-next-line playwright/no-conditional-expect
      await expect(page).toPassAxe();
    }
    await page.evaluate(() => document.fonts.ready);
    await page.getByLabel(labels.title, { exact: true }).focus();
    const screenshot = testInfo.outputPath("draft.png");
    await page.screenshot({ path: screenshot, fullPage: true });
    await testInfo.attach(`svelte-${locale}`, {
      path: screenshot,
      contentType: "image/png",
    });
    await expect(page).toHaveScreenshot(`trial-svelte-${locale}.png`, { fullPage: true });
    await page.getByLabel(labels.category).selectOption("utility");
    await expect(page.getByLabel(labels.width)).toHaveCount(0);
    await page.getByLabel(labels.title, { exact: true }).fill("Reserved");
    await page.getByRole("button", { name: labels.save, exact: true }).click();
    await expect(page.getByLabel(labels.title, { exact: true })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  test(`${locale} / dialog keyboard behavior`, async ({ page }) => {
    await page.goto(`/design-system?lang=${locale}`);
    const trigger = page.getByRole("button", { name: labels.dialog, exact: true });
    await trigger.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(page.getByRole("button", { name: labels.close })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByRole("button", { name: labels.save, exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: labels.close })).toBeFocused();
    await expect(page).toPassAxe();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });

  test(`${locale} / server-rendered form`, async ({ browser }) => {
    const context = await browser.newContext({ javaScriptEnabled: false });
    try {
      const page = await context.newPage();
      await page.goto(`${APP_URL}/design-system-trial?lang=${locale}`);
      await expect(page.getByLabel(labels.title, { exact: true })).toBeVisible();
      await expect(page.getByLabel(labels.width)).toBeVisible();
      await expect(page.getByLabel(labels.version, { exact: true })).toBeVisible();
    } finally {
      await context.close();
    }
  });
}
