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
    locale: import("@/locales/locale").Locale;
    /**
     * The path and query the visitor asked for, with its locale prefix. Unlike `Astro.url`, it
     * survives the rewrites to /zh-cn routes and error pages, so links back to this page use it.
     */
    requestedPath: string;
    /** The theme the visitor pinned with the theme cookie; undefined follows the device. */
    theme: import("@/ui/themes").Theme | undefined;
    /** The contrast level the visitor pinned; undefined follows the device. */
    contrast: import("@/ui/themes").Contrast | undefined;
    config: import("@/server/config").RuntimeConfig;
    /** Generated SDK client for this request's locale; pass it as `client` to SDK functions. */
    api: import("@/generated/client").Client;
    /**
     * Who is signed in, from `GET /v1/session`; null when the visitor sent no session cookie.
     * A snapshot with a null user means the cookie expired.
     */
    session: import("@/generated/types.gen").SessionSnapshot | null;
  }
}
