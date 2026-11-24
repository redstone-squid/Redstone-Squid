import type { Page } from "@playwright/test";
import * as m from "@/locales/messages.gen";
import { expect, test } from "@tests/support/test";

const html = (page: Page) => page.locator("html");
const themeButton = (page: Page, name: string) =>
  page.getByRole("group", { name: m.footerTheme("zh-CN") }).getByRole("button", { name });

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

    await themeButton(page, m.footerThemeSystem("zh-CN")).click();
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

test("the theme form only returns to pages on this site", async ({ request }) => {
  const response = await request.post("/theme", {
    form: { theme: "dark", return: "//evil.example/records" },
    // Astro's origin check lets same-site browser posts through by this header.
    headers: { "Sec-Fetch-Site": "same-origin" },
    maxRedirects: 0,
  });
  expect(response.status()).toBe(303);
  expect(response.headers().location).toBe("/");
});
