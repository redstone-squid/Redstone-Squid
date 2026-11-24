import type { Page } from "@playwright/test";
import { LOCALES, localizePath } from "@/locales/locale";
import * as m from "@/locales/messages.gen";
import { GROUPS } from "@/workbench/groups";
import { expect, test } from "@tests/support/test";
import { captureForReview } from "@tests/support/review-screenshot";

async function settle(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
    // Lazy images below the fold never load in a full-page capture unless asked to.
    for (const image of document.images) image.loading = "eager";
    await Promise.all(Array.from(document.images, (image) => image.decode().catch(() => null)));
  });
}

async function fitsViewport(page: Page): Promise<boolean> {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
}

for (const theme of ["light", "dark"] as const) {
  for (const locale of ["en", "zh-CN"] as const) {
    test(`${locale} / ${theme} / foundations`, async ({ page }, testInfo) => {
      await page.goto(localizePath(`/design-system?theme=${theme}`, locale));
      await settle(page);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.locator('meta[name="robots"]')).toHaveAttribute(
        "content",
        "noindex,nofollow",
      );
      expect(await fitsViewport(page)).toBe(true);
      await expect(page).toPassAxe();
      const name = `foundations-${locale}-${theme}`;
      await captureForReview(page, testInfo, name, "disabled");
    });
  }

  for (const group of Object.keys(GROUPS)) {
    test(`${theme} / ${group}`, async ({ page }, testInfo) => {
      await page.goto(`/design-system/${group}?theme=${theme}`);
      await settle(page);
      const title = GROUPS[group as keyof typeof GROUPS].title("en");
      await expect(page.getByRole("heading", { level: 1, name: title, exact: true })).toBeVisible();
      expect(await fitsViewport(page)).toBe(true);
      await expect(page).toPassAxe();
      const name = `${group}-${theme}`;
      await captureForReview(page, testInfo, name, "disabled");
    });
  }
}

test.describe("with high contrast", () => {
  test.use({ contrast: "more" });

  for (const theme of ["light", "dark"] as const) {
    for (const group of ["data", "feedback", "forms"] as const) {
      test(`${theme} / ${group}`, async ({ page }, testInfo) => {
        await page.goto(`/design-system/${group}?theme=${theme}`);
        await settle(page);
        await expect(page).toPassAxe();
        await captureForReview(page, testInfo, `${group}-${theme}-high-contrast`, "disabled");
      });
    }
  }
});

test.describe("with JavaScript disabled", () => {
  test.use({ javaScriptEnabled: false });

  test("renders stories on the server and switches theme with a link", async ({ page }) => {
    await page.goto("/design-system/patterns?theme=dark");
    await expect(page.getByRole("heading", { name: "Copper Bolt 5×5", level: 3 })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.goto("/design-system?theme=dark");
    await page.getByRole("link", { name: m.workbenchLightTheme("en") }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await expect(page).toHaveURL(/\?theme=light$/);
    const contrast = page.getByRole("group", { name: m.footerContrast("en"), exact: true });
    await contrast.getByRole("link", { name: m.footerContrastMore("en") }).click();
    await expect(page.locator("html")).toHaveAttribute("data-contrast", "more");
    await page
      .getByRole("navigation")
      .getByRole("link", { name: m.trialTitle("en") })
      .click();
    await expect(page).toHaveURL(/design-system-trial\?theme=light&contrast=more$/);
    await contrast.getByRole("link", { name: m.workbenchDefaultContrast("en") }).click();
    await expect(page).toHaveURL(/design-system-trial\?theme=light$/);
    expect(
      (await page.context().cookies()).filter(({ name }) => /theme|contrast/.test(name)),
    ).toEqual([]);
  });
});

test("keeps the theme and query when changing language", async ({ page }) => {
  await page.goto("/design-system/data?theme=dark");
  await page.getByLabel(`${LOCALES.en.label} · ${m.navLanguage("en")}`).click();
  await page.getByRole("link", { name: LOCALES["zh-CN"].label }).click();
  await expect(page).toHaveURL(/\/zh-cn\/design-system\/data\?theme=dark$/);
  await expect(
    page.getByRole("heading", { level: 1, name: GROUPS.data.title("zh-CN") }),
  ).toBeVisible();
  await page.getByText(m.storyDoorEvidence("zh-CN"), { exact: true }).click();
  await expect(page.getByText(m.storyMeasuredWithATickCounterOnJava1("zh-CN"))).toBeVisible();
});

test("redirects the old media story to the data stories", async ({ page }) => {
  await page.goto("/design-system/media?theme=dark");
  await expect(page).toHaveURL(/\/design-system\/data\?theme=dark$/);
});

test("reflows at 320px and retains controls in forced colours", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/zh-cn/design-system/overlays");
  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: m.workbenchSkipToContent("zh-CN") });
  await expect(skipLink).toBeFocused();
  await expect(skipLink).toBeInViewport();
  expect(await fitsViewport(page)).toBe(true);
  const newDraft = page.getByRole("button", { name: m.workbenchNewDraft("zh-CN") });
  await expect(newDraft).toBeEnabled();
  await newDraft.click();
  await expect(page.getByRole("dialog")).toBeVisible();
});

test("chooses a fixture suggestion using the keyboard", async ({ page }) => {
  await page.goto("/design-system/forms");
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

test("shows image fallbacks after hydration", async ({ page }) => {
  await page.goto("/design-system/data");
  await expect(page.getByText("Preview could not be loaded")).toBeVisible();
});
