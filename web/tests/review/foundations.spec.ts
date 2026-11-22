import { localizePath } from "@/locales/locale";
import { expect, test } from "../support/test";
import { APP_URL } from "../support/servers";

for (const locale of ["en", "zh-CN"] as const) {
  for (const theme of ["light", "dark"]) {
    test(`${locale} / ${theme} / foundations`, async ({ page }, testInfo) => {
      await page.goto(localizePath(`/design-system?theme=${theme}`, locale));
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
      await expect(page).toPassAxe();
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
    await page.goto(`${APP_URL}/design-system`);
    await expect(page.getByRole("heading", { name: "Design system" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Copper Bolt 5×5" })).toBeVisible();
    await page.getByRole("combobox", { name: "Variant", exact: true }).selectOption("quiet");
    await page.getByRole("combobox", { name: "Size", exact: true }).selectOption("small");
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    const preview = page.locator("#button-story").getByRole("link");
    await expect(preview).toHaveClass(/sq-button--quiet/);
    await expect(preview).toHaveClass(/sq-button--small/);
    await page.getByRole("link", { name: "Dark theme", exact: true }).click();
    await expect(preview).toHaveClass(/sq-button--quiet/);
    await preview.click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.getByRole("link", { name: "Foundations", exact: true }).click();
    await expect(page).toHaveURL(/design-system\?.*#foundations$/);
    await page.getByRole("link", { name: "中文", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  } finally {
    await context.close();
  }
});

test("reflows at 320px and retains controls in forced colours", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/zh-cn/design-system");
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

test("shares the selected field state and layout", async ({ page }) => {
  await page.goto("/design-system?theme=dark&layout=row#fields");
  const fields = page.getByRole("region", { name: "Form controls" });
  await fields.getByRole("link", { name: "Invalid" }).click();
  const category = fields.getByRole("combobox", { name: "Category" });
  await expect(category).toHaveAttribute("aria-invalid", "true");
  await expect(category).toHaveAccessibleDescription("This field is required.");
  await fields.getByRole("link", { name: "Disabled" }).click();
  await expect(category).toBeDisabled();
  await expect(fields.getByRole("radio", { name: "Java", exact: true })).toBeDisabled();

  const layouts = page.getByRole("region", { name: "Layouts", exact: true });
  await expect(layouts.getByRole("link", { name: "Row", exact: true })).toHaveAttribute(
    "aria-current",
    "true",
  );
  await layouts.getByRole("link", { name: "Stack", exact: true }).click();
  await layouts.getByRole("link", { name: "Compact", exact: true }).click();
  await expect(layouts.locator("code")).toContainText('<Layout pattern="stack" gap="compact">');
  await page.reload();
  await expect(category).toBeDisabled();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("chooses a fixture suggestion using the keyboard", async ({ page }) => {
  await page.goto("/design-system#components");
  const input = page.getByRole("combobox", { name: "Minecraft version", exact: true });
  await input.fill("Java");
  await expect(page.getByRole("option", { name: "Java 1.21.4" })).toBeVisible();
  await input.press("ArrowDown");
  await input.press("Enter");
  await expect(input).toHaveValue("Java 1.21.4");
  await expect(input).toHaveAttribute("aria-expanded", "false");
});
