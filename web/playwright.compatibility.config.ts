import { defineConfig, devices } from "@playwright/test";
import review from "./playwright.review.config";

export default defineConfig({
  ...review,
  testMatch: "draft-trial.spec.ts",
  // Visual baselines are captured in Chromium only.
  ignoreSnapshots: true,
  outputDir: "test-results/compatibility",
  reporter: [
    ["line"],
    ["html", { outputFolder: "playwright-report/compatibility", open: "never" }],
  ],
  projects: [
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
