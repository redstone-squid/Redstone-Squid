import path from "node:path";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [svelte({ configFile: false })],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
      "@tests": path.resolve(import.meta.dirname, "tests"),
    },
    conditions: ["browser"],
  },
  test: {
    environment: "jsdom",
    include: ["tests/unit/**/*.test.ts"],
    setupFiles: ["./tests/setup.ts"],
    coverage: {
      provider: "v8",
      include: [
        "src/locales/**/*.ts",
        "src/ui/**/*.{ts,svelte}",
        "src/features/**/*.{ts,svelte}",
        "src/workbench/**/*.{ts,svelte}",
      ],
      exclude: ["src/generated/**", "**/*.gen.ts"],
      reporter: ["text", "json-summary"],
      thresholds: {
        lines: 90,
        functions: 90,
        statements: 90,
        branches: 85,
      },
    },
  },
});
