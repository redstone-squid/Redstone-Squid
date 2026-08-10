const DEVELOPMENT_DEFAULTS = {
  API_BASE_URL: "http://127.0.0.1:8000",
  SITE_URL: "http://127.0.0.1:4321",
  DISCORD_COMMUNITY_URL: "https://discord.gg/redstone",
  BOT_INVITE_URL: "https://discord.com/oauth2/authorize",
} as const;

export type RuntimeConfig = {
  apiBaseUrl: string;
  siteUrl: string;
  discordCommunityUrl: string;
  botInviteUrl: string;
};

export type RuntimeEnvironment = Partial<
  Record<keyof typeof DEVELOPMENT_DEFAULTS, string | undefined>
>;

function checkedUrl(
  name: keyof typeof DEVELOPMENT_DEFAULTS,
  value: string,
  production: boolean,
): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${name} must be an absolute HTTP(S) URL.`);
  }
  if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
    throw new Error(`${name} must use HTTP or HTTPS.`);
  }
  if (
    production &&
    (parsed.hostname === "localhost" ||
      parsed.hostname === "127.0.0.1" ||
      parsed.hostname.endsWith(".invalid") ||
      parsed.hostname.includes("example"))
  ) {
    throw new Error(`${name} contains a development or placeholder host.`);
  }
  return parsed.toString().replace(/\/$/, "");
}

export function getRuntimeConfig(
  environment: RuntimeEnvironment = process.env,
  production = import.meta.env.PROD,
): RuntimeConfig {
  const value = (name: keyof typeof DEVELOPMENT_DEFAULTS): string => {
    const configured = environment[name]?.trim();
    if (production && !configured) {
      throw new Error(`${name} is required in production.`);
    }
    return checkedUrl(name, configured ?? DEVELOPMENT_DEFAULTS[name], production);
  };

  return {
    apiBaseUrl: value("API_BASE_URL"),
    siteUrl: value("SITE_URL"),
    discordCommunityUrl: value("DISCORD_COMMUNITY_URL"),
    botInviteUrl: value("BOT_INVITE_URL"),
  };
}
