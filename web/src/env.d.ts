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
    responseStatus?: number;
  }
}
