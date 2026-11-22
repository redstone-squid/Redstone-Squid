import { localizePath } from "@/locales/locale";
import * as m from "@/locales/messages.gen";
import { test, expect } from "@tests/support/test";
import { attachReviewScreenshot } from "@tests/support/review-screenshot";

test.use({ video: "on" });
for (const locale of ["en", "zh-CN"] as const) {
  test(`${locale} / draft workflow`, async ({ page, browserName }, testInfo) => {
    await page.goto(localizePath("/design-system-trial?scenario=retry", locale));
    const title = page.getByRole("textbox", { name: m.workbenchBuildTitle(locale) });
    const width = page.getByRole("textbox", { name: m.trialWidth(locale) });
    const version = page.getByRole("combobox", { name: m.workbenchMinecraftVersion(locale) });
    const save = page.getByRole("button", { name: m.workbenchSaveDraft(locale), exact: true });
    await save.click();
    await expect(title).toHaveAttribute("aria-invalid", "true");
    await expect(title).toHaveAccessibleDescription(m.trialRequired(locale));
    await title.fill("Copper Bolt");
    await width.fill("5");
    await page.getByRole("searchbox", { name: m.trialFindVersion(locale) }).fill("1.21.4");
    await expect(version.getByRole("option", { name: "Bedrock 1.21.50" })).toHaveCount(0);
    await version.selectOption("Java 1.21.4");
    const filename = "door.schem";
    await page.getByLabel(m.trialUpload(locale)).setInputFiles({
      name: filename,
      mimeType: "application/octet-stream",
      buffer: Buffer.from("fixture"),
    });
    await expect(page.getByRole("progressbar")).toBeVisible();
    await expect(save).toBeDisabled();
    await expect(page.getByText(m.trialAttachment({ filename }, locale))).toBeVisible();
    await save.click();
    await page.getByRole("button", { name: m.trialRetry(locale), exact: true }).click();
    await expect(page.getByRole("status")).toHaveText(m.trialSaved(locale));
    await expect(page.locator("astro-island")).toHaveCount(1);
    await expect(title).toHaveValue("Copper Bolt");
    // The review suite runs axe in Chromium; compatibility projects exercise the workflow.
    // Playwright's Firefox 1572 crashes in axe on this form.
    // eslint-disable-next-line playwright/no-conditional-in-test
    if (browserName === "chromium") {
      // eslint-disable-next-line playwright/no-conditional-expect
      await expect(page).toPassAxe();
    }
    await page.evaluate(() => document.fonts.ready);
    await title.focus();
    await attachReviewScreenshot(page, testInfo, `svelte-${locale}`, "allow");
    await expect(page).toHaveScreenshot(`trial-svelte-${locale}.png`, { fullPage: true });
    await page.getByRole("combobox", { name: m.trialCategory(locale) }).selectOption("utility");
    await expect(width).toHaveCount(0);
    await title.fill("Reserved");
    await save.click();
    await expect(title).toHaveAccessibleDescription(m.trialReserved(locale));
    await expect(title).toHaveAttribute("aria-invalid", "true");
  });

  test(`${locale} / dialog keyboard behavior`, async ({ page }) => {
    await page.goto(localizePath("/design-system", locale));
    const trigger = page.getByRole("button", { name: m.workbenchNewDraft(locale), exact: true });
    await trigger.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    const close = dialog.getByRole("button", { name: m.workbenchCloseDialog(locale) });
    const save = dialog.getByRole("button", { name: m.workbenchSaveDraft(locale) });
    await expect(dialog).toBeVisible();
    await expect(close).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(save).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(close).toBeFocused();
    await expect(page).toPassAxe();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });

  test.describe(`${locale} with JavaScript disabled`, () => {
    test.use({ javaScriptEnabled: false });

    test("renders the form on the server", async ({ page }) => {
      await page.goto(localizePath("/design-system-trial", locale));
      await expect(
        page.getByRole("textbox", { name: m.workbenchBuildTitle(locale) }),
      ).toBeVisible();
      await expect(page.getByRole("textbox", { name: m.trialWidth(locale) })).toBeVisible();
      await expect(
        page.getByRole("combobox", { name: m.workbenchMinecraftVersion(locale) }),
      ).toBeVisible();
    });
  });
}
