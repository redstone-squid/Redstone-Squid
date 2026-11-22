import { LOCALES } from "@/locales/locale";
import * as m from "@/locales/messages.gen";
import { localizePath } from "@/locales/locale";
import { buttonStyles } from "@/ui/variants";
import { expect, test } from "@tests/support/test";
import { attachReviewScreenshot } from "@tests/support/review-screenshot";

for (const locale of ["en", "zh-CN"] as const) {
  for (const theme of ["light", "dark"]) {
    test(`${locale} / ${theme} / foundations`, async ({ page }, testInfo) => {
      await page.goto(localizePath(`/design-system?theme=${theme}`, locale));
      await page.evaluate(async () => {
        await document.fonts.ready;
        // Lazy images below the fold never load in a full-page capture unless asked to.
        for (const image of document.images) image.loading = "eager";
        await Promise.all(Array.from(document.images, (image) => image.decode()));
      });
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.locator('meta[name="robots"]')).toHaveAttribute(
        "content",
        "noindex,nofollow",
      );
      const draft = page.getByRole("button", { name: m.workbenchNewDraft(locale), exact: true });
      await draft.scrollIntoViewIfNeeded();
      await expect(draft).toBeEnabled();
      await page.evaluate(() => window.scrollTo(0, 0));
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      await expect(page).toPassAxe();
      const name = `${locale}-${theme}`;
      await attachReviewScreenshot(page, testInfo, name, "disabled");
      await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true });
    });
  }
}

test.describe("with JavaScript disabled", () => {
  test.use({ javaScriptEnabled: false });

  test("public workbench content renders without JavaScript", async ({ page }) => {
    await page.goto("/design-system?theme=dark&variant=quiet&size=small");
    await expect(page.getByRole("heading", { name: m.workbenchPageTitle("en") })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Copper Bolt 5×5" })).toBeVisible();
    const preview = page.locator("#button-story").getByRole("link");
    await expect(preview).toContainClass(buttonStyles({ variant: "quiet", size: "small" }));
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });
});

test("updates button previews and theme without navigating", async ({ page }) => {
  await page.goto("/design-system#button-story");
  const navigations: string[] = [];
  page.on("request", (request) => {
    if (request.isNavigationRequest()) navigations.push(request.url());
  });
  const variant = page.getByRole("combobox", { name: m.workbenchVariant("en") });
  const size = page.getByRole("combobox", { name: m.workbenchSize("en") });
  const preview = page.locator("#button-story").getByRole("link");
  await variant.selectOption("quiet");
  await size.selectOption("small");
  await expect(preview).toContainClass(buttonStyles({ variant: "quiet", size: "small" }));
  await expect(page).toHaveURL(/\?variant=quiet&size=small#button-story$/);

  const darkTheme = page.getByRole("button", { name: m.workbenchDarkTheme("en") });
  await darkTheme.focus();
  await darkTheme.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("button", { name: m.workbenchLightTheme("en") })).toBeFocused();
  await expect(preview).toHaveAttribute("href", "/design-system-trial?theme=dark");
  await expect(
    page.getByRole("complementary").getByRole("link", { name: m.trialTitle("en") }),
  ).toHaveAttribute("href", /\/design-system-trial\?theme=dark$/);
  await page.getByRole("link", { name: m.workbenchFoundations("en"), exact: true }).click();
  await expect(page).toHaveURL(/\?variant=quiet&size=small&theme=dark#foundations$/);
  expect(navigations).toEqual([]);

  const header = page.getByRole("banner");
  await header.getByLabel(`${LOCALES.en.label} · ${m.navLanguage("en")}`).click();
  await header.getByRole("link", { name: LOCALES["zh-CN"].label, exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(preview).toContainClass(buttonStyles({ variant: "quiet", size: "small" }));
});

test("reflows at 320px and retains controls in forced colours", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/zh-cn/design-system");
  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: m.workbenchSkipToContent("zh-CN") });
  await expect(skipLink).toBeFocused();
  await expect(skipLink).toHaveCSS("clip-path", "none");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  const newDraft = page.getByRole("button", { name: m.workbenchNewDraft("zh-CN") });
  await newDraft.scrollIntoViewIfNeeded();
  await expect(newDraft).toBeVisible();
  await newDraft.click();
  await expect(page.getByRole("dialog")).toBeVisible();
});

test("changes field state and layout in place and restores them from the URL", async ({ page }) => {
  await page.goto("/design-system?theme=dark&layout=row#fields");
  const navigations: string[] = [];
  page.on("request", (request) => {
    if (request.isNavigationRequest()) navigations.push(request.url());
  });
  const fields = page.getByRole("region", { name: m.workbenchFormControls("en") });
  const invalid = fields.getByRole("button", { name: m.workbenchInvalid("en") });
  await page.evaluate(() => document.fonts.ready);
  await invalid.focus();
  const scroll = await page.evaluate(() => window.scrollY);
  await invalid.press("Enter");
  const category = fields.getByRole("combobox", { name: m.trialCategory("en") });
  await expect(category).toHaveAttribute("aria-invalid", "true");
  await expect(category).toHaveAccessibleDescription(m.trialRequired("en"));
  await expect(invalid).toBeFocused();
  expect(await page.evaluate(() => window.scrollY)).toBe(scroll);
  await fields.getByRole("button", { name: m.workbenchDisabled("en") }).click();
  await expect(category).toBeDisabled();
  await expect(fields.getByRole("radio", { name: "Java", exact: true })).toBeDisabled();

  const layouts = page.getByRole("region", { name: m.workbenchLayouts("en"), exact: true });
  await expect(
    layouts.getByRole("button", { name: m.workbenchRow("en"), exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await layouts.getByRole("button", { name: m.workbenchStack("en"), exact: true }).click();
  await layouts.getByRole("button", { name: m.workbenchCompact("en"), exact: true }).click();
  await expect(layouts.locator("code")).toContainText('<Layout pattern="stack" gap="compact">');
  expect(navigations).toEqual([]);
  await page.reload();
  await expect(category).toBeDisabled();
  await expect(layouts.locator("code")).toContainText('<Layout pattern="stack" gap="compact">');
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("chooses a fixture suggestion using the keyboard", async ({ page }) => {
  await page.goto("/design-system#components");
  const input = page.getByRole("combobox", {
    name: m.workbenchMinecraftVersion("en"),
    exact: true,
  });
  await input.fill("Java");
  await expect(page.getByRole("option", { name: "Java 1.21.4" })).toBeVisible();
  await input.press("ArrowDown");
  await input.press("Enter");
  await expect(input).toHaveValue("Java 1.21.4");
  await expect(input).toHaveAttribute("aria-expanded", "false");
});
