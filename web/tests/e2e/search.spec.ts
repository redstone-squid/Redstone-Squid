import { expect, test } from "@tests/support/test";

for (const prefix of ["", "/zh-cn"]) {
  test(`header search opens catalogue results at ${prefix}/search`, async ({ page }) => {
    await page.goto(`${prefix}/about`);
    const header = page.getByRole("banner");
    const trigger = header.locator('[popovertarget="header-search-field"]');
    await page.setViewportSize({ width: 390, height: 800 });
    await trigger.click();
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

test("keyboard shortcut focuses and selects the wide header query", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/search?q=door");
  const modifier = await page.evaluate(() =>
    /Mac|iPhone|iPad/.test(navigator.platform) ? "Meta" : "Control",
  );
  await page.keyboard.press(`${modifier}+k`);
  const input = page.getByRole("banner").getByRole("searchbox");
  await expect(input).toBeFocused();
  await page.keyboard.type("flush");
  await expect(input).toHaveValue("flush");
});
