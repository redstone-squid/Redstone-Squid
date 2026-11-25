import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { test as base, expect as baseExpect, type Page } from "@playwright/test";

/**
 * Playwright `test` and `expect` for every browser spec.
 *
 * Each test fails if the page logs a console error or throws, which catches hydration
 * failures that assertions miss. `await expect(page).toPassAxe()` fails on any axe violation.
 * Fixture media URLs on media.fixture.invalid are served locally; `broken.png` is not an image,
 * so pages can show their broken-image fallback.
 */
const fixtureImage = readFileSync("public/fixtures/copper-door.svg");

export const test = base.extend<{ browserDiagnostics: undefined; fixtureMedia: undefined }>({
  fixtureMedia: [
    async ({ page }, use) => {
      await page.route("https://media.fixture.invalid/**", (route) =>
        route.fulfill(
          route.request().url().endsWith("/broken.png")
            ? { contentType: "image/png", body: "not an image" }
            : { contentType: "image/svg+xml", body: fixtureImage },
        ),
      );
      await use(undefined);
    },
    { auto: true },
  ],
  browserDiagnostics: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        // WebKit refuses speculation-rule prefetches over HTTP and logs it as an error. The test
        // servers are HTTP, and production is HTTPS, so this says nothing about the page.
        if (message.type() === "error" && !message.text().startsWith("Prefetch request denied"))
          errors.push(message.text());
      });
      await use(undefined);
      baseExpect(errors, "Unexpected browser or hydration errors").toEqual([]);
    },
    { auto: true },
  ],
});

export const expect = baseExpect.extend({
  async toPassAxe(page: Page) {
    const { violations } = await new AxeBuilder({ page }).analyze();
    const summary = violations.map(
      (violation) =>
        `${violation.id}: ${violation.help} (${violation.nodes.map((node) => node.target.join(" ")).join(", ")})`,
    );
    return {
      name: "toPassAxe",
      pass: violations.length === 0,
      message: () =>
        this.isNot ? "Expected axe violations" : `Axe violations:\n${summary.join("\n")}`,
    };
  },
});
