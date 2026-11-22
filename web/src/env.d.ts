/// <reference types="astro/client" />

interface ImportMetaEnv {
  readonly API_BASE_URL?: string;
  readonly SITE_URL?: string;
  readonly DISCORD_COMMUNITY_URL?: string;
  readonly BOT_INVITE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare namespace App {
  interface Locals {
    /** Request locale, resolved from the URL prefix by middleware. */
    locale: import("./lib/i18n").Locale;
    config: import("./server/config").RuntimeConfig;
    /** Generated SDK client for this request's locale; pass it as `client` to SDK functions. */
    api: import("./generated/client").Client;
  }
}
