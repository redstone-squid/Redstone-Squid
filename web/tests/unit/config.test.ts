import { describe, expect, it } from "vitest";

import { getRuntimeConfig } from "../../src/lib/config";

const productionEnvironment = {
  API_BASE_URL: "https://api.redstone-squid.org/",
  SITE_URL: "https://catalogue.redstone-squid.org/",
  DISCORD_COMMUNITY_URL: "https://discord.gg/redstone",
  BOT_INVITE_URL: "https://discord.com/oauth2/authorize?client_id=123",
};

describe("runtime configuration", () => {
  it("uses local defaults only outside production", () => {
    expect(getRuntimeConfig({}, false)).toEqual({
      apiBaseUrl: "http://127.0.0.1:8000",
      siteUrl: "http://127.0.0.1:4321",
      discordCommunityUrl: "https://discord.gg/redstone",
      botInviteUrl: "https://discord.com/oauth2/authorize",
    });
  });

  it("normalizes validated production values", () => {
    expect(getRuntimeConfig(productionEnvironment, true)).toEqual({
      apiBaseUrl: "https://api.redstone-squid.org",
      siteUrl: "https://catalogue.redstone-squid.org",
      discordCommunityUrl: "https://discord.gg/redstone",
      botInviteUrl: "https://discord.com/oauth2/authorize?client_id=123",
    });
  });

  it.each([
    [{ ...productionEnvironment, API_BASE_URL: undefined }, "API_BASE_URL is required"],
    [{ ...productionEnvironment, SITE_URL: "http://localhost:4321" }, "placeholder host"],
    [{ ...productionEnvironment, SITE_URL: "https://example.com" }, "placeholder host"],
    [{ ...productionEnvironment, SITE_URL: "not a url" }, "absolute HTTP(S) URL"],
    [{ ...productionEnvironment, SITE_URL: "file:///tmp/catalogue" }, "HTTP or HTTPS"],
  ])("rejects unsafe configuration %#", (environment, message) => {
    expect(() => getRuntimeConfig(environment, true)).toThrow(message);
  });
});
