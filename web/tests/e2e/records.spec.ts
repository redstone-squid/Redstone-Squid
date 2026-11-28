import * as m from "@/locales/messages.gen";
import { recordCriterionLabel } from "@/locales/vocabulary.gen";
import { expect, test } from "@tests/support/test";

// Fixture records from tests/fixtures/api.ts.
const fastestDoor = "Fastest 5×5 seamless door";
const extender = "Smallest piston extender";

test("lists records and pages through them", async ({ page }) => {
  await page.goto("/records");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(m.recordsTitle("en"));
  await expect(page.getByRole("link", { name: fastestDoor })).toBeVisible();
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "index,follow");
  await expect(page).toPassAxe();

  const pages = page.getByRole("navigation", { name: m.navPagination("en") });
  await pages.getByRole("link", { name: m.commonNext("en") }).click();
  await expect(page).toHaveURL(/\/records\?page=2$/);
  await expect(page.getByRole("link", { name: extender })).toBeVisible();
  // Later pages repeat the first page's content in another order, so they are not indexed.
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex,follow");
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
    "href",
    "https://catalogue.redstone-squid.org/records",
  );

  await pages.getByRole("link", { name: m.commonPrevious("en") }).click();
  await expect(page.getByRole("link", { name: fastestDoor })).toBeVisible();
});

test("shows a record's classification and holders", async ({ page }) => {
  await page.goto("/records");
  await page.getByRole("link", { name: fastestDoor }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(fastestDoor);
  await expect(page.getByText(recordCriterionLabel("fastest_smallest", "en"))).toBeVisible();
  const holders = page.getByRole("heading", { level: 2, name: "Copper Bolt" });
  await holders.getByRole("link", { name: "Copper Bolt" }).click();
  await expect(page).toHaveURL(/\/builds\/1$/);
});

test("renders records in the visitor's locale", async ({ page }) => {
  await page.goto("/zh-cn/records/11111111-1111-1111-1111-111111111111");
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.getByText(recordCriterionLabel("fastest_smallest", "zh-CN"))).toBeVisible();
  await expect(page.getByRole("link", { name: "Copper Bolt" })).toBeVisible();
  await expect(page).toPassAxe();
});

test("answers 404 for a record that does not exist", async ({ request }) => {
  const response = await request.get("/records/99999999-9999-9999-9999-999999999999");
  expect(response.status()).toBe(404);
});
