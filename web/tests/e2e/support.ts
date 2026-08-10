import { expect, type Page } from "@playwright/test";

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

export async function installMediaRoutes(page: Page): Promise<void> {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("https://**/*", async (route) => route.abort("blockedbyclient"));
  await page.route("https://media.fixture.invalid/**", async (route) => {
    if (route.request().url().endsWith("/broken.png")) {
      await route.abort("failed");
      return;
    }
    await route.fulfill({ status: 200, contentType: "image/png", body: PNG });
  });
}

export async function expectCatalogueMetadata(page: Page, canonicalPath: string): Promise<void> {
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
    "href",
    `https://catalogue.redstone-squid.org${canonicalPath}`,
  );
  await expect(page.locator('link[hreflang="en"]')).toHaveCount(1);
  await expect(page.locator('link[hreflang="zh-CN"]')).toHaveCount(1);
  await expect(page.locator('meta[property="og:title"]')).toHaveAttribute("content", /.+/);
}
