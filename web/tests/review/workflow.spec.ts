import * as m from "@/locales/messages.gen";
import { expect, test } from "@tests/support/test";
test.use({ video: "on", reducedMotion: "no-preference" });

test.describe("workflow recording", () => {
  test("create a draft / validation and keyboard recovery", async ({ page }, testInfo) => {
    await page.goto("/design-system/overlays");
    const trigger = page.getByRole("button", { name: m.workbenchNewDraft("en"), exact: true });
    await trigger.scrollIntoViewIfNeeded();
    await expect(trigger).toBeEnabled();
    await trigger.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    const save = dialog.getByRole("button", { name: m.workbenchSaveDraft("en") });
    const title = dialog.getByRole("textbox", { name: m.workbenchBuildTitle("en") });
    await expect(dialog).toBeVisible();
    await expect(title).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(title).toHaveAttribute("aria-invalid", "true");
    await expect(title).toHaveAccessibleDescription(m.workbenchEnterATitleForYourBuild("en"));
    const screenshot = testInfo.outputPath("validation.png");
    await page.screenshot({ path: screenshot, animations: "disabled" });
    await testInfo.attach("Draft validation", { path: screenshot, contentType: "image/png" });
    await expect(page).toPassAxe();
    await title.fill("Copper Bolt 5×5");
    await save.click();
    await expect(dialog.getByRole("status")).toHaveText(m.workbenchExampleDraftSaved("en"));
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  });
});
