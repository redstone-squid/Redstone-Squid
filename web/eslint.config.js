import eslint from "@eslint/js";
import vitest from "@vitest/eslint-plugin";
import astro from "eslint-plugin-astro";
import playwright from "eslint-plugin-playwright";
import svelte from "eslint-plugin-svelte";
import globals from "globals";
import tseslint from "typescript-eslint";
import { importBoundaries } from "./scripts/lint/import-boundaries.ts";
import { noRawText } from "./scripts/lint/no-raw-text.ts";

const typedFiles = ["**/*.ts", "**/*.svelte"];
// The typed presets also set the TypeScript parser, which would replace Svelte's parser,
// so Svelte files take only their rules.
const typedRules = Object.assign(
  {},
  ...[...tseslint.configs.strictTypeChecked, ...tseslint.configs.stylisticTypeChecked].map(
    (config) => config.rules ?? {},
  ),
);

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
      "**/*.gen.ts",
      "playwright-report/**",
      "test-results/**",
      "review-gallery/**",
    ],
  },
  eslint.configs.recommended,
  {
    rules: {
      eqeqeq: ["error", "smart"],
      "no-console": ["error", { allow: ["info", "warn", "error"] }],
      "no-param-reassign": "error",
      "object-shorthand": "error",
      "prefer-template": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "TSAsExpression > TSAsExpression",
          message: "A double assertion hides a type error. Fix the types or decode the value.",
        },
        {
          selector: "AssignmentExpression[left.property.name=/^(innerHTML|outerHTML)$/]",
          message: "Render markup through Svelte or Astro so it is escaped.",
        },
        {
          selector:
            "ImportDeclaration[source.value=/locales\\/messages\\.gen$/] > :matches(ImportSpecifier, ImportDefaultSpecifier, ImportNamespaceSpecifier[local.name!='m'])",
          message: 'Import messages as `import * as m from "@/locales/messages.gen"`.',
        },
      ],
    },
  },
  ...tseslint.configs.strictTypeChecked.map((config) => ({ ...config, files: ["**/*.ts"] })),
  ...tseslint.configs.stylisticTypeChecked.map((config) => ({ ...config, files: ["**/*.ts"] })),
  ...svelte.configs.recommended,
  {
    files: ["**/*.svelte"],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { parser: tseslint.parser, extraFileExtensions: [".svelte"] },
    },
    plugins: { "@typescript-eslint": tseslint.plugin },
    rules: {
      ...typedRules,
      // Linting cannot resolve the prop types of imported components, so values they pass
      // back look like `any`. svelte-check type-checks these files instead.
      "@typescript-eslint/no-unsafe-argument": "off",
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-return": "off",
      // `let { ... } = $props()` must use let; svelte/prefer-const understands runes.
      "prefer-const": "off",
      "no-unused-vars": "off",
      "svelte/block-lang": ["error", { script: "ts" }],
      "svelte/button-has-type": "error",
      "svelte/no-inline-styles": "error",
      "svelte/no-target-blank": "error",
      "svelte/prefer-const": "error",
    },
  },
  {
    files: typedFiles,
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      "@typescript-eslint/consistent-type-definitions": ["error", "type"],
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
      "@typescript-eslint/no-confusing-void-expression": "off",
      "@typescript-eslint/no-import-type-side-effects": "error",
      "@typescript-eslint/prefer-readonly": "error",
      "@typescript-eslint/require-array-sort-compare": ["error", { ignoreStringArrays: true }],
      "@typescript-eslint/restrict-template-expressions": [
        "error",
        { allowNumber: true, allowBoolean: true, allowNullish: false },
      ],
      "@typescript-eslint/strict-boolean-expressions": [
        "error",
        { allowString: false, allowNumber: false, allowNullableBoolean: true },
      ],
      "@typescript-eslint/switch-exhaustiveness-check": [
        "error",
        { considerDefaultExhaustiveForUnions: true },
      ],
    },
  },
  {
    files: ["**/*.ts"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
  },
  ...tseslint.configs.recommended.map((config) => ({ ...config, files: ["**/*.astro"] })),
  ...astro.configs.recommended,
  ...astro.configs["jsx-a11y-recommended"],
  {
    ...tseslint.configs.disableTypeChecked,
    files: ["**/*.astro", "**/*.astro/*.{js,ts}"],
  },
  {
    files: ["**/*.astro"],
    rules: {
      "astro/no-exports-from-components": "error",
      "astro/no-set-html-directive": "error",
      "astro/no-unsafe-inline-scripts": "error",
    },
  },
  {
    files: ["src/features/builds/BuildPreview.astro"],
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
    files: ["src/**/*.{ts,astro,svelte}", "src/**/*.astro/*.{js,ts}"],
    plugins: {
      squid: { rules: { "import-boundaries": importBoundaries, "no-raw-text": noRawText } },
    },
    rules: {
      "squid/import-boundaries": "error",
      "no-restricted-imports": [
        "error",
        { patterns: [{ group: ["../*"], message: "Import from outside this directory with @/." }] },
      ],
    },
  },
  {
    // Messages format numbers and dates with NUMBER() and DATETIME(), so translators own them.
    files: ["src/**/*.{ts,astro,svelte}"],
    ignores: ["src/locales/format.ts"],
    rules: {
      "no-restricted-globals": [
        "error",
        { name: "Intl", message: "Format inside a message with NUMBER() or DATETIME()." },
      ],
      "no-restricted-properties": [
        "error",
        ...["toLocaleString", "toLocaleDateString", "toLocaleTimeString"].map((property) => ({
          property,
          message: "Format inside a message with NUMBER() or DATETIME().",
        })),
      ],
    },
  },
  {
    // The workbench shows fixture content, and src/ui receives its labels through props.
    files: ["src/{pages,layouts,features}/**/*.{astro,svelte}"],
    ignores: ["src/pages/design-system*", "src/pages/design-system/**"],
    rules: { "squid/no-raw-text": "error" },
  },
  {
    files: ["tests/unit/**/*.ts"],
    ...vitest.configs.recommended,
    rules: {
      ...vitest.configs.recommended.rules,
      "vitest/consistent-test-it": ["error", { fn: "it", withinDescribe: "it" }],
      "vitest/no-conditional-tests": "error",
      "vitest/prefer-hooks-on-top": "error",
      "vitest/prefer-to-be": "error",
      "vitest/prefer-to-have-length": "error",
      "vitest/require-to-throw-message": "error",
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
    },
  },
  {
    files: ["tests/{e2e,review}/**/*.ts"],
    ...playwright.configs["flat/recommended"],
    rules: {
      ...playwright.configs["flat/recommended"].rules,
      "playwright/no-commented-out-tests": "error",
      "playwright/no-skipped-test": ["error", { allowConditional: true }],
      "playwright/no-slowed-test": "error",
      "playwright/prefer-comparison-matcher": "error",
      "playwright/prefer-equality-matcher": "error",
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@playwright/test",
              importNames: ["test", "expect"],
              message: "Import test and expect from tests/support/test.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["**/*.d.ts"],
    rules: {
      // Ambient declarations cannot use import statements without becoming modules.
      "@typescript-eslint/consistent-type-imports": "off",
      "@typescript-eslint/consistent-type-definitions": "off",
      "@typescript-eslint/triple-slash-reference": "off",
    },
  },
);
