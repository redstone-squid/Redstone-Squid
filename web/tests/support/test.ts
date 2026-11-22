import AxeBuilder from "@axe-core/playwright";
import { test as base, expect as baseExpect, type Page } from "@playwright/test";

/**
 * Playwright `test` and `expect` for every browser spec.
 *
 * Each test fails if the page logs a console error or throws, which catches hydration
 * failures that assertions miss. `await expect(page).toPassAxe()` fails on any axe violation.
 */
export const test = base.extend<{ browserDiagnostics: undefined }>({
  browserDiagnostics: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
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
