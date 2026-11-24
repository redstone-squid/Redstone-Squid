import { expect, test } from "@tests/support/test";

test("the first visible plot fits its reserved space", async ({ page }) => {
  const scripts = Promise.withResolvers<undefined>();
  await page.route("**/ChartStories.*.js", async (route) => {
    await scripts.promise;
    await route.continue();
  });
  await page.goto("/design-system/chart", { waitUntil: "commit" });
  const plot = page.getByRole("figure", { name: "Record progression" }).locator("[role=group]");
  try {
    await expect(plot).toHaveCSS("visibility", "hidden");
    await expect(plot).toHaveCSS("height", "240px");
  } finally {
    scripts.resolve(undefined);
  }
  await expect(plot).toBeVisible();
  await expect(plot).toHaveCSS("height", "240px");
});

test.describe("without JavaScript", () => {
  test.use({ javaScriptEnabled: false });

  test("the server-rendered plot is visible", async ({ page }) => {
    await page.goto("/design-system/chart");
    await expect(
      page.getByRole("group", { name: "Record progression", exact: true }),
    ).toBeVisible();
  });
});
