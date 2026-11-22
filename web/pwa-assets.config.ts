import { defineConfig, minimal2023Preset } from "@vite-pwa/assets-generator/config";

// Regenerate the install icons in public/ from the favicon: bun run pwa:icons
export default defineConfig({
  preset: { ...minimal2023Preset, maskable: { ...minimal2023Preset.maskable, padding: 0.2 } },
  images: ["public/favicon.svg"],
});
