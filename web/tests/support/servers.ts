/**
 * The fixture API and built site that Playwright and Lighthouse run against. Set
 * `TEST_API_PORT` and `TEST_APP_PORT` to give each checkout its own servers, since local runs
 * reuse whatever already listens on these ports.
 */
export const API_PORT = Number(process.env.TEST_API_PORT ?? 8787);
const APP_PORT = Number(process.env.TEST_APP_PORT ?? 4321);
export const APP_URL = `http://127.0.0.1:${APP_PORT}`;

export const FIXTURE_API_COMMAND = ["bun", "tests/fixtures/api.ts"] as const;
export const fixtureApiEnvironment = { FIXTURE_API_PORT: String(API_PORT) };

export const appEnvironment = {
  HOST: "127.0.0.1",
  PORT: String(APP_PORT),
  // Production config rejects 127.0.0.1, and WebKit rejects the wildcard address, so use
  // another loopback address. The fixture API listens on all interfaces.
  API_BASE_URL: `http://127.0.0.2:${API_PORT}`,
  SITE_URL: "https://catalogue.redstone-squid.org",
  DISCORD_COMMUNITY_URL: "https://discord.gg/redstone",
  BOT_INVITE_URL: "https://discord.com/oauth2/authorize?client_id=fixture",
};
