import node from "@astrojs/node";
import svelte from "@astrojs/svelte";
import react from "@astrojs/react";
import { defineConfig } from "astro/config";

export default defineConfig({
  adapter: node({ mode: "standalone" }),
  integrations: [react(), svelte()],
  output: "server",
  security: {
    checkOrigin: true,
  },
  vite: {
    plugins: [
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
