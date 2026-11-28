import { expect, test } from "@tests/support/test";

for (const prefix of ["", "/zh-cn"]) {
  test(`header search opens catalogue results at ${prefix}/search`, async ({ page }) => {
    await page.goto(`${prefix}/about`);
    const header = page.getByRole("banner");
    const trigger = header.locator('[popovertarget="header-search-field"]');
    if (await trigger.isVisible()) await trigger.click();
    else
      await page.keyboard.press(
        (await page.evaluate(() => /Mac/.test(navigator.platform))) ? "Meta+k" : "Control+k",
      );
    const query = header.getByRole("searchbox");
    await expect(query).toBeFocused();
    await query.fill("door");
    await query.press("Enter");
    await expect(page).toHaveURL(new RegExp(`${prefix}/search\\?q=door$`));
    const main = page.getByRole("main");
    await expect(main.getByRole("searchbox")).toHaveValue("door");
    await expect(main.locator(`a[href^="${prefix}/builds/"]`).first()).toBeVisible();
  });
}
