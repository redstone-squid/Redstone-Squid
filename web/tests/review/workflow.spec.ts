import { expect, test } from "../support/test";
test.use({ video: "on", reducedMotion: "no-preference" });

test.describe("workflow recording", () => {
  test("create a draft / validation and keyboard recovery", async ({ page }, testInfo) => {
    await page.goto("/design-system");
    const trigger = page.getByRole("button", { name: "New draft", exact: true });
    await trigger.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(page.getByRole("button", { name: "Close dialog" })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByRole("button", { name: "Save draft", exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Close dialog" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Build title")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("Build title")).toHaveAttribute("aria-invalid", "true");
    const screenshot = testInfo.outputPath("validation.png");
    await page.screenshot({ path: screenshot, animations: "disabled" });
    await testInfo.attach("Draft validation", { path: screenshot, contentType: "image/png" });
    await expect(page).toPassAxe();
    await page.getByLabel("Build title").fill("Copper Bolt 5×5");
    await page.getByRole("button", { name: "Save draft", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("Example draft saved.");
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });
});
