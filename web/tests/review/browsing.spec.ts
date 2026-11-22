import { LOCALES } from "@/locales/locale";
import * as m from "@/locales/messages.gen";
import { expect, test } from "../support/test";

test("browse sample records, change language, and open the media placeholder", async ({ page }) => {
  await page.goto("/design-system?theme=dark#browsing");
  const story = page.getByRole("region", { name: m.workbenchBrowsing("en"), exact: true });
  await story.getByRole("link", { name: m.commonLoadMore("en") }).click();
  await expect(story.getByRole("heading", { name: m.workbenchSeamlessDoor("en") })).toBeVisible();
  await expect(story.getByText(m.recordRefreshPending("en"))).toBeVisible();
  await story.getByRole("link", { name: m.commonPrevious("en") }).click();
  await expect(story.getByRole("heading", { name: m.workbenchPistonDoor("en") })).toBeVisible();
  await story.getByLabel(`${LOCALES.en.label} · ${m.navLanguage("en")}`).click();
  await story.getByRole("link", { name: LOCALES["zh-CN"].label }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await page.getByRole("link", { name: m.workbenchMediaStory("zh-CN") }).click();
  await expect(page).toHaveURL((url) => url.pathname === "/zh-cn/design-system/media");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("main").getByText(m.placeholderRebuilding("zh-CN"))).toBeVisible();
  await expect(page).toPassAxe();
});
