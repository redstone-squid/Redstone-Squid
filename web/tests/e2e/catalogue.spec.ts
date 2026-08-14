import { expect, test } from "@playwright/test";

import { expectCatalogueMetadata, installMediaRoutes } from "./support";

test.beforeEach(async ({ page }) => installMediaRoutes(page));

test("serves meaningful navigation and catalogue cards without JavaScript", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await installMediaRoutes(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Redstone engineering, documented.",
  );
  await expect(page.getByRole("link", { name: "Copper Bolt 5×5" })).toBeVisible();
  await page.getByRole("link", { name: "Builds", exact: true }).first().click();
  await expect(page).toHaveURL(/\/builds$/);
  await expect(page.getByRole("heading", { name: "Build catalogue" })).toBeVisible();
  await context.close();
});

test("switches locale while preserving query and page state", async ({ page }) => {
  await page.goto("/builds?q=door&after_id=2");
  const language = page.getByRole("link", { name: "简体中文" });
  await expect(language).toHaveAttribute("href", "/zh-cn/builds?q=door&after_id=2");
  await language.click();
  await expect(page).toHaveURL(/\/zh-cn\/builds\?q=door&after_id=2/);
  await expect(page.getByRole("heading", { name: "作品目录" })).toBeVisible();
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex,follow");
});

test("guided search creates a shareable creator query", async ({ page }) => {
  await page.goto("/builds");
  await page.getByLabel("Creator").fill("Space Builder");
  await page.getByLabel("Category").selectOption("entrance");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page).toHaveURL(/creator%3A%22Space\+Builder%22/);
  await expect(page.getByRole("link", { name: "Observerless Iris" })).toBeVisible();
  await expect(page.getByText("Copper Bolt 5×5")).toHaveCount(0);
  expect(new URL(page.url()).searchParams.has("status")).toBe(false);
});

test("advanced invalid syntax renders the localized problem instead of an empty page", async ({
  page,
}) => {
  const response = await page.goto("/search?q=syntax%3Aerror");
  expect(response?.status()).toBe(400);
  await expect(page.getByText("HTTP 400")).toBeVisible();
  await expect(page.getByText("Check the search syntax and try again.")).toBeVisible();
  await page.goto("/zh-cn/search?q=syntax%3Aerror");
  await expect(page.getByText("请检查搜索语法后重试。")).toBeVisible();
});

test("pagination is a real link with a first-page canonical", async ({ page }) => {
  await page.goto("/builds?q=door");
  const more = page.getByRole("link", { name: "Load more" });
  await expect(more).toHaveAttribute("href", /after_id=2/);
  await more.click();
  await expect(page.getByRole("link", { name: "Slim Piston Extender" })).toBeVisible();
  await expectCatalogueMetadata(page, "/builds?q=door");
});

test("renders build specifications, safe media, downloads, and schematic analysis", async ({
  page,
}) => {
  await page.goto("/builds/1");
  await expect(page.getByRole("heading", { level: 1, name: "Copper Bolt 5×5" })).toBeVisible();
  await expect(page.getByRole("definition").filter({ hasText: "9 × 7 × 4" })).toBeVisible();
  await expect(page.getByText("184 blocks")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Coppercraft" })).toBeVisible();
  await expect(page.getByText("play.coppercraft.example")).toBeVisible();
  await expect(page.getByRole("link", { name: "https://coppercraft.example/" })).toHaveAttribute(
    "href",
    "https://coppercraft.example/",
  );
  await expect(page.getByRole("link", { name: "CircuitSage" }).last()).toHaveAttribute(
    "href",
    "/creators/CircuitSage",
  );
  await expect(page.getByRole("button", { name: "Next media" })).toBeVisible();
  await page.getByRole("button", { name: "Next media" }).click();
  await expect(page.getByText("Item 2 of 2")).toBeVisible();
  await expectCatalogueMetadata(page, "/builds/1");
  expect(await page.locator('script[type="application/ld+json"]').textContent()).toContain(
    "CreativeWork",
  );
});

test("uses a branded fallback for broken preview media", async ({ page }) => {
  await page.goto("/builds");
  const brokenCard = page.getByRole("article").filter({ hasText: "Observerless Iris" });
  await expect(brokenCard.getByText("Image unavailable")).toBeVisible();
});

test("renders authoritative record holders and creator searches", async ({ page }) => {
  await page.goto("/records/11");
  await expect(page.getByRole("heading", { name: "Fastest 5×5 seamless door" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Copper Bolt 5×5" })).toBeVisible();
  await page.goto("/creators/Space%20Builder");
  await expect(page.getByRole("heading", { name: "Builds by Space Builder" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Observerless Iris" })).toBeVisible();
});

test("renders build, record, and metadata search results", async ({ page }) => {
  await page.goto("/search?q=door");
  await expect(page.getByRole("link", { name: "Copper Bolt 5×5" })).toBeVisible();
  await expect(page.getByText("Fastest 5×5 seamless door")).toBeVisible();
  await expect(
    page
      .getByRole("article")
      .filter({ hasText: "Catalogue term" })
      .getByText("Seamless", { exact: true }),
  ).toBeVisible();
});

test("suggestions are keyboard selectable through the same-origin proxy", async ({ page }) => {
  await page.goto("/search");
  const input = page.getByLabel("Keywords");
  await input.fill("do");
  await expect(page.getByRole("option", { name: "door", exact: true })).toBeVisible();
  await input.press("ArrowDown");
  await input.press("Enter");
  await expect(input).toHaveValue("door");
});

test("returns explicit not-found and service states", async ({ page }) => {
  const missing = await page.goto("/builds/999");
  expect(missing?.status()).toBe(404);
  await expect(
    page.getByRole("heading", { name: "That catalogue page was not found" }),
  ).toBeVisible();
  const unavailable = await page.goto("/503");
  expect(unavailable?.status()).toBe(503);
  await expect(
    page.getByRole("heading", { name: "The catalogue service is unavailable" }),
  ).toBeVisible();
});

test("maps an upstream timeout to service unavailable", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "The timeout path only needs one browser engine.");
  const timeout = await page.goto("/builds/998");
  expect(timeout?.status()).toBe(503);
  await expect(page.getByText("HTTP 503")).toBeVisible();
});

test("publishes robots and a dynamic bilingual sitemap", async ({ request }) => {
  const robots = await request.get("/robots.txt");
  expect(await robots.text()).toContain(
    "Sitemap: https://catalogue.redstone-squid.org/sitemap.xml",
  );
  const sitemap = await request.get("/sitemap.xml");
  expect(sitemap.status()).toBe(200);
  const xml = await sitemap.text();
  expect(xml).toContain("/builds/1");
  expect(xml).toContain("/zh-cn/builds/1");
  expect(xml).toContain('hreflang="zh-CN"');
});
