import { defineConfig, devices } from "@playwright/test";

const APP_PORT = 4321;
const API_PORT = 8787;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "list",
  expect: {
    timeout: 15_000,
    toHaveScreenshot: { maxDiffPixelRatio: 0.001, animations: "disabled" },
  },
  snapshotPathTemplate: "{testDir}/__screenshots__/{projectName}/{arg}{ext}",
  use: {
    baseURL: `http://127.0.0.1:${APP_PORT}`,
    locale: "en-US",
    serviceWorkers: "block",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: [
    {
      command: "bun tests/fixtures/api.ts",
      url: `http://127.0.0.1:${API_PORT}/livez`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      env: { FIXTURE_API_PORT: String(API_PORT) },
    },
    {
      command: "bun run start",
      url: `http://127.0.0.1:${APP_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      env: {
        HOST: "127.0.0.1",
        PORT: String(APP_PORT),
        API_BASE_URL: `http://0.0.0.0:${API_PORT}`,
        SITE_URL: "https://catalogue.redstone-squid.org",
        DISCORD_COMMUNITY_URL: "https://discord.gg/redstone",
        BOT_INVITE_URL: "https://discord.com/oauth2/authorize?client_id=fixture",
      },
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
