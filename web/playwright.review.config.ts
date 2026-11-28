import { defineConfig, devices } from "@playwright/test";
import catalogue from "./playwright.config";

export default defineConfig({
  ...catalogue,
  testDir: "./tests/review",
  // Headless Chromium repaints the catalogue group's tall page of shadowed cards slowly, which
  // the reflow sweep repeats at 54 widths.
  timeout: 120_000,
  outputDir: "test-results/review",
  snapshotPathTemplate: "{testDir}/__screenshots__/{projectName}/{arg}{ext}",
  reporter: [
    ["line"],
    ["html", { outputFolder: "playwright-report/review", open: "never" }],
    ["./tests/review/gallery-reporter.ts"],
  ],
  use: { ...catalogue.use, timezoneId: "UTC", reducedMotion: "reduce" },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } },
    },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
