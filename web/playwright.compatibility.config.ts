import { defineConfig, devices } from "@playwright/test";
import review from "./playwright.review.config";

export default defineConfig({
  ...review,
  testMatch: ["framework-trial.spec.ts", "discovery.spec.ts"],
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
