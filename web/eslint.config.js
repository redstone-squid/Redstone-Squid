import eslint from "@eslint/js";
import svelte from "eslint-plugin-svelte";
import astro from "eslint-plugin-astro";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";
import { importBoundaries } from "./scripts/import-boundaries.ts";

export default tseslint.config(
  {
    ignores: [
      ".bun-cache/**",
      ".bun-tmp/**",
      ".astro/**",
      ".playwright-browsers/**",
      "dist/**",
      "eslint.config.js",
      "coverage/**",
      "src/generated/**",
      "playwright-report/**",
      "test-results/**",
      "review-gallery/**",
    ],
  },
  eslint.configs.recommended,
  ...tseslint.configs.strictTypeChecked.map((config) => ({ ...config, files: ["**/*.{ts,tsx}"] })),
  ...tseslint.configs.stylisticTypeChecked.map((config) => ({
    ...config,
    files: ["**/*.{ts,tsx}"],
  })),
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "jsx-a11y": jsxA11y,
      "react-hooks": reactHooks,
    },
    rules: {
      ...jsxA11y.flatConfigs.recommended.rules,
      ...reactHooks.configs.flat.recommended.rules,
      "@typescript-eslint/consistent-type-definitions": ["error", "type"],
      "@typescript-eslint/no-confusing-void-expression": "off",
      "@typescript-eslint/restrict-template-expressions": [
        "error",
        { allowNumber: true, allowBoolean: true, allowNullish: false },
      ],
    },
  },
  ...tseslint.configs.recommended.map((config) => ({ ...config, files: ["**/*.astro"] })),
  ...svelte.configs.recommended,
  {
    files: ["**/*.svelte"],
    languageOptions: { globals: globals.browser, parserOptions: { parser: tseslint.parser } },
    plugins: { "@typescript-eslint": tseslint.plugin },
    rules: { "no-unused-vars": "off", "@typescript-eslint/no-unused-vars": "error" },
  },
  ...astro.configs.recommended,
  ...astro.configs["jsx-a11y-recommended"],
  {
    ...tseslint.configs.disableTypeChecked,
    files: ["**/*.astro", "**/*.astro/*.{js,ts}"],
  },
  {
    files: ["src/features/builds/components/BuildPreview.astro"],
    rules: {
      // The Astro adapter flags img's onerror despite the recommended img lifecycle exception.
      // Keep keyboard and pointer checks; error/load only handle image loading, not user input.
      "astro/jsx-a11y/no-noninteractive-element-interactions": [
        "error",
        {
          handlers: ["onClick", "onMouseDown", "onMouseUp", "onKeyPress", "onKeyDown", "onKeyUp"],
        },
      ],
    },
  },
  {
    files: ["src/**/*.{ts,tsx,astro,svelte}", "src/**/*.astro/*.{js,ts}"],
    plugins: { boundaries: { rules: { imports: importBoundaries } } },
    rules: { "boundaries/imports": "error" },
  },
  {
    files: ["**/*.test.{ts,tsx}", "tests/**/*.ts"],
    rules: {
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
    },
  },
  {
    files: ["**/*.d.ts"],
    rules: {
      "@typescript-eslint/consistent-type-definitions": "off",
      "@typescript-eslint/triple-slash-reference": "off",
    },
  },
);
