import { defineConfig, devices } from "@playwright/test";
import {
  API_PORT,
  APP_URL,
  FIXTURE_API_COMMAND,
  appEnvironment,
  fixtureApiEnvironment,
} from "./tests/support/servers";

const CI = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: CI,
  retries: CI ? 2 : 0,
  workers: 1,
  reporter: CI ? [["line"], ["html", { open: "never" }]] : "list",
  expect: {
    timeout: 15_000,
    toHaveScreenshot: { maxDiffPixelRatio: 0.001, animations: "disabled" },
  },
  snapshotPathTemplate: "{testDir}/__screenshots__/{projectName}/{arg}{ext}",
  use: {
    baseURL: APP_URL,
    locale: "en-US",
    serviceWorkers: "block",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: [
    {
      command: FIXTURE_API_COMMAND.join(" "),
      url: `http://127.0.0.1:${API_PORT}/livez`,
      reuseExistingServer: !CI,
      timeout: 30_000,
      env: fixtureApiEnvironment,
    },
    {
      command: "bun run start",
      url: APP_URL,
      reuseExistingServer: !CI,
      timeout: 30_000,
      env: appEnvironment,
    },
  ],
  projects: [
    { name: "chromium-desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox-desktop", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit-desktop", use: { ...devices["Desktop Safari"] } },
    { name: "mobile-chrome", use: { ...devices["Pixel 7"] } },
    { name: "mobile-safari", use: { ...devices["iPhone 15"] } },
  ],
});
