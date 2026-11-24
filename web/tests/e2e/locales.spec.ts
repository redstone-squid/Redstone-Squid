import { LOCALES } from "@/locales/locale";
import * as m from "@/locales/messages.gen";
import { expect, test } from "@tests/support/test";

test("the language selector preserves a detail URL and its query", async ({ page }) => {
  await page.goto("/tags/zero-tick?view=compact");
  const header = page.getByRole("banner");
  await header.getByLabel(`${LOCALES.en.label} · ${m.navLanguage("en")}`).click();
  await header.getByRole("link", { name: LOCALES["zh-CN"].label }).click();
  await expect(page).toHaveURL("/zh-cn/tags/zero-tick?view=compact");
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
});

test("the language menu closes on Escape and on a click outside it", async ({ page }) => {
  await page.goto("/tags/zero-tick");
  const trigger = page
    .getByRole("banner")
    .getByRole("button", { name: `${LOCALES.en.label} · ${m.navLanguage("en")}` });
  const menu = page.getByRole("list", { name: m.navLanguage("en") });

  await trigger.click();
  await expect(menu).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(trigger).toBeFocused();

  await trigger.click();
  await expect(menu).toBeVisible();
  await page.getByRole("main").click({ position: { x: 5, y: 5 } });
  await expect(menu).toBeHidden();
});

test("the pseudo-locale formats interface text and stays out of search and menus", async ({
  page,
}) => {
  await page.goto("/en-xa/tags/zero-tick");
  await expect(page.locator("html")).toHaveAttribute("lang", "en-XA");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex,follow");
  await expect(page.locator('link[hreflang="en-XA"]')).toHaveCount(0);
  await page.getByLabel(/^\[.*\] · \[.*\]$/).click();
  await expect(page.getByRole("list", { name: /^\[.*\]$/ }).getByRole("link")).toHaveText([
    "English",
    "简体中文",
  ]);
});
