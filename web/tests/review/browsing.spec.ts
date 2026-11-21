import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "./test";

test("browse sample records, change language, and open the media placeholder", async ({ page }) => {
  await page.goto("/design-system?theme=dark#browsing");
  const story = page.getByRole("region", { name: "Browsing", exact: true });
  await story.getByRole("link", { name: "Load more" }).click();
  await expect(story.getByRole("heading", { name: /Seamless door/ })).toBeVisible();
  await expect(story.getByText(/Refresh pending/)).toBeVisible();
  await story.getByRole("link", { name: "Previous" }).click();
  await expect(story.getByRole("heading", { name: /Piston door/ })).toBeVisible();
  await story.locator("summary").click();
  await story.getByRole("link", { name: "简体中文" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await page.getByRole("link", { name: "查看媒体图库" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByText("红石鱿鱼网站正在重建。")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
