const DEVELOPMENT_DEFAULTS = {
  API_BASE_URL: "http://127.0.0.1:8000",
  SITE_URL: "http://127.0.0.1:4321",
  DISCORD_COMMUNITY_URL: "https://discord.gg/redstone",
  BOT_INVITE_URL: "https://discord.com/oauth2/authorize",
} as const;

export type RuntimeConfig = {
  /** The API as browsers reach it: links into the OAuth flow and island requests. */
  apiBaseUrl: string;
  /**
   * The API as this server reaches it, from `API_INTERNAL_URL`, defaulting to `apiBaseUrl`.
   * Production points it at the API container so page requests skip the public gateway, which
   * would replace the `X-Forwarded-For` chain naming the visitor.
   */
  apiInternalUrl: string;
  siteUrl: string;
  discordCommunityUrl: string;
  botInviteUrl: string;
};

type Setting = keyof typeof DEVELOPMENT_DEFAULTS | "API_INTERNAL_URL";

export type RuntimeEnvironment = Partial<Record<Setting, string | undefined>>;

function checkedUrl(name: Setting, value: string, production: boolean): string {
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
  return parsed.href.replace(/\/$/, "");
}

/**
 * Read and normalize runtime HTTP(S) URLs, removing a trailing slash.
 *
 * Production requires all four public settings and rejects known development/placeholder hosts.
 * Missing production values or invalid URLs throw; development uses defaults for absent values.
 * `API_INTERNAL_URL` is optional everywhere.
 */
export function getRuntimeConfig(
  environment: RuntimeEnvironment = process.env,
  production = import.meta.env.PROD,
): RuntimeConfig {
  const configuredValue = (name: Setting): string | undefined => {
    // An empty variable counts as unset, so development falls back to the default.
    const trimmed = environment[name]?.trim();
    return trimmed === "" ? undefined : trimmed;
  };
  const value = (name: keyof typeof DEVELOPMENT_DEFAULTS): string => {
    const configured = configuredValue(name);
    if (production && configured === undefined) {
      throw new Error(`${name} is required in production.`);
    }
    return checkedUrl(name, configured ?? DEVELOPMENT_DEFAULTS[name], production);
  };

  const apiBaseUrl = value("API_BASE_URL");
  const internal = configuredValue("API_INTERNAL_URL");
  return {
    apiBaseUrl,
    apiInternalUrl:
      internal === undefined ? apiBaseUrl : checkedUrl("API_INTERNAL_URL", internal, production),
    siteUrl: value("SITE_URL"),
    discordCommunityUrl: value("DISCORD_COMMUNITY_URL"),
    botInviteUrl: value("BOT_INVITE_URL"),
  };
}
