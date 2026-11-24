import type { Page } from "@playwright/test";
import * as m from "@/locales/messages.gen";
import { expect, test } from "@tests/support/test";

const html = (page: Page) => page.locator("html");
const option = (page: Page, group: string, name: string) =>
  page.getByRole("group", { name: group }).getByRole("button", { name });
const themeButton = (page: Page, name: string) => option(page, m.footerTheme("zh-CN"), name);
const contrastButton = (page: Page, name: string) => option(page, m.footerContrast("zh-CN"), name);
// Dividers change color with contrast, so the footer's top border shows which tokens apply.
const dividerColor = (page: Page) =>
  page.locator("footer").evaluate((footer) => getComputedStyle(footer).borderTopColor);

test.describe("with JavaScript disabled", () => {
  test.use({ javaScriptEnabled: false });

  test("pins a theme with a form post and returns to the localized page", async ({ page }) => {
    await page.goto("/zh-cn/builds?view=compact");
    await expect(html(page)).not.toHaveAttribute("data-theme");
    await themeButton(page, m.footerThemeDark("zh-CN")).click();
    await expect(page).toHaveURL("/zh-cn/builds?view=compact");
    await expect(html(page)).toHaveAttribute("data-theme", "dark");
    await expect(themeButton(page, m.footerThemeDark("zh-CN"))).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await themeButton(page, m.footerSystem("zh-CN")).click();
    await expect(html(page)).not.toHaveAttribute("data-theme");
  });
});

test("switches in place and keeps the theme on the next page", async ({ page, context }) => {
  await page.goto("/zh-cn/builds");
  await themeButton(page, m.footerThemeLight("zh-CN")).click();
  await expect(html(page)).toHaveAttribute("data-theme", "light");
  await expect(page).toHaveURL("/zh-cn/builds");
  const saved = async () => (await context.cookies()).find(({ name }) => name === "theme")?.value;
  await expect.poll(saved).toBe("light");
  await page.goto("/zh-cn/records");
  await expect(html(page)).toHaveAttribute("data-theme", "light");
});

test("the workbench query overrides the pinned theme", async ({ page, context, baseURL }) => {
  await context.addCookies([{ name: "theme", value: "dark", url: baseURL ?? "" }]);
  await page.goto("/design-system");
  await expect(html(page)).toHaveAttribute("data-theme", "dark");
  await page.goto("/design-system?theme=light");
  await expect(html(page)).toHaveAttribute("data-theme", "light");
});

test("the appearance form only returns to pages on this site", async ({ request }) => {
  const response = await request.post("/appearance", {
    form: { theme: "dark", return: "//evil.example/records" },
    // Astro's origin check lets same-site browser posts through by this header.
    headers: { "Sec-Fetch-Site": "same-origin" },
    maxRedirects: 0,
  });
  expect(response.status()).toBe(303);
  expect(response.headers().location).toBe("/");
});

test.describe("on a device that asks for more contrast", () => {
  test.use({ contrast: "more" });

  test("uses high contrast until the visitor pins standard", async ({ page }) => {
    await page.goto("/zh-cn/builds");
    const high = await dividerColor(page);
    await contrastButton(page, m.footerContrastStandard("zh-CN")).click();
    await expect(html(page)).toHaveAttribute("data-contrast", "standard");
    await expect.poll(() => dividerColor(page)).not.toBe(high);
    await page.reload();
    await expect.poll(() => dividerColor(page)).not.toBe(high);
  });
});

test("pins high contrast on a device without the preference", async ({ page }) => {
  await page.goto("/zh-cn/builds");
  const standard = await dividerColor(page);
  await contrastButton(page, m.footerContrastMore("zh-CN")).click();
  await expect(html(page)).toHaveAttribute("data-contrast", "more");
  await expect.poll(() => dividerColor(page)).not.toBe(standard);
  // Contrast is independent of the theme.
  await expect(html(page)).not.toHaveAttribute("data-theme");
});
