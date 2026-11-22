import node from "@astrojs/node";
import svelte from "@astrojs/svelte";
import tailwindcss from "@tailwindcss/vite";
import { AstroPWAIntegration } from "@vite-pwa/astro";
import { defineConfig } from "astro/config";
import { PUBLIC_LOCALES, localizePath } from "./src/locales/locale";

// Changes on every build so installed workers refetch the server-rendered offline pages.
const revision = process.env.GITHUB_SHA ?? String(Date.now());

export default defineConfig({
  adapter: node({ mode: "standalone" }),
  integrations: [
    svelte(),
    // Installable app with an offline page; pages themselves always come from the network.
    // See src/sw.ts. The icons come from public/favicon.svg via `bun run pwa:icons`.
    AstroPWAIntegration({
      strategies: "buildSW",
      injectRegister: false,
      additionalManifestEntries: PUBLIC_LOCALES.map((locale) => ({
        url: localizePath("/offline", locale),
        revision,
      })),
      manifest: {
        name: "Redstone Squid",
        short_name: "Redstone Squid",
        description: "Minecraft redstone builds and records.",
        start_url: "/",
        display: "standalone",
        background_color: "#f5f5f4",
        theme_color: "#f5f5f4",
        icons: [
          { src: "pwa-64x64.png", sizes: "64x64", type: "image/png" },
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          {
            src: "maskable-icon-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      buildSW: { swSrc: "src/sw.ts", globPatterns: ["_astro/*.css"] },
    }),
  ],
  output: "server",
  security: {
    checkOrigin: true,
  },
  vite: {
    plugins: [
      tailwindcss(),
      {
        name: "squid:client-budget-manifest",
        configEnvironment(name) {
          if (name === "client") return { build: { manifest: true } };
          return undefined;
        },
      },
    ],
    build: {
      cssMinify: true,
    },
  },
});
