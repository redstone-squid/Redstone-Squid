import node from "@astrojs/node";
import svelte from "@astrojs/svelte";
import tailwindcss from "@tailwindcss/vite";
import { AstroPWAIntegration } from "@vite-pwa/astro";
import { defineConfig } from "astro/config";
import { PUBLIC_LOCALES, localizePath } from "./src/locales/locale";
import { THEME_COLORS } from "./src/ui/themes";

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
        background_color: THEME_COLORS.light,
        theme_color: THEME_COLORS.light,
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
      // The offline page needs its styles and the logo in the header.
      buildSW: { swSrc: "src/sw.ts", globPatterns: ["_astro/*.css", "favicon.svg"] },
    }),
  ],
  output: "server",
  security: {
    checkOrigin: true,
    // Astro hashes the scripts and styles it emits and sends the policy as a header on
    // server-rendered pages. Not applied under `astro dev`.
    csp: {
      directives: [
        "default-src 'self'",
        // Build previews and avatars are user-submitted HTTPS URLs on any host.
        "img-src 'self' https:",
        // The workbench's specimen fonts are inlined into its stylesheet.
        "font-src 'self' data:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        // The endpoint is named in the Reporting-Endpoints header set by src/middleware.ts.
        "report-to csp",
      ],
      styleDirective: {
        resources: [
          "'self'",
          // Components put data-driven sizes in style attributes, such as a meter's fill.
          // An attribute cannot hold selectors, so this only lets injected markup restyle itself.
          { resource: "'unsafe-inline'", kind: "attribute" },
        ],
      },
    },
  },
  vite: {
    plugins: [
      tailwindcss(),
      {
        name: "squid:client-budget-manifest",
        configEnvironment(name) {
          return name === "client" ? { build: { manifest: true } } : undefined;
        },
      },
    ],
    build: {
      cssMinify: true,
    },
  },
});
