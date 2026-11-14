import { test as base, expect } from "@playwright/test";

export { expect };
export const test = base.extend<{ browserDiagnostics: undefined }>({
  browserDiagnostics: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
      });
      await use(undefined);
      expect(errors, "Unexpected browser or hydration errors").toEqual([]);
    },
    { auto: true },
  ],
});
