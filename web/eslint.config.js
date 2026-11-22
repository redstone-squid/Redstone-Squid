import eslint from "@eslint/js";
import vitest from "@vitest/eslint-plugin";
import astro from "eslint-plugin-astro";
import betterTailwind from "eslint-plugin-better-tailwindcss";
import playwright from "eslint-plugin-playwright";
import svelte from "eslint-plugin-svelte";
import globals from "globals";
import tseslint from "typescript-eslint";
import { importBoundaries } from "./scripts/lint/import-boundaries.ts";
import { noRawText } from "./scripts/lint/no-raw-text.ts";

// Shared so blocks that add restrictions keep these.
const restrictedSyntax = [
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
];

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
      "no-restricted-syntax": ["error", ...restrictedSyntax],
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
    // Class names must exist in the theme, and colors come from tokens rather than arbitrary
    // values. Prettier sorts classes, so ordering and wrapping rules stay off.
    files: ["src/**/*.{ts,astro,svelte}"],
    plugins: { "better-tailwindcss": betterTailwind },
    settings: {
      "better-tailwindcss": {
        entryPoint: "src/ui/app.css",
        callees: ["cva", "cn", "cx"],
      },
    },
    rules: {
      "better-tailwindcss/no-unknown-classes": "error",
      "better-tailwindcss/no-conflicting-classes": "error",
      "better-tailwindcss/no-duplicate-classes": "error",
      "better-tailwindcss/no-deprecated-classes": "error",
      "better-tailwindcss/no-unnecessary-whitespace": "error",
      "better-tailwindcss/no-restricted-classes": [
        "error",
        {
          restrict: [
            {
              pattern:
                "^(?:.*:)?(?:bg|text|border|outline|ring|fill|stroke|accent|caret|decoration|shadow|from|via|to)-\\[(?:#|rgb|hsl|oklch|color)",
              message: "Use a theme color from src/ui/themes.css instead of an arbitrary color.",
            },
          ],
        },
      ],
    },
  },
  {
    // prettier-plugin-tailwindcss sorts classes in Svelte and TypeScript but not in Astro files
    // with prettier-plugin-astro 1.x, so ESLint sorts them there (fixable with --fix).
    files: ["src/**/*.astro"],
    rules: { "better-tailwindcss/enforce-consistent-class-order": "error" },
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
    // Formatting and cookies each have one owner. Messages format numbers and dates with
    // NUMBER() and DATETIME(), so translators control them; src/server/cookies.ts owns every
    // cookie's name and flags. The two owners are exempt.
    files: ["src/**/*.{ts,astro,svelte}"],
    ignores: ["src/locales/format.ts", "src/server/cookies.ts"],
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
        ...[
          ["Astro", "cookies"],
          ["context", "cookies"],
          ["document", "cookie"],
        ].map(([object, property]) => ({
          object,
          property,
          message: "Read and write cookies through src/server/cookies.ts.",
        })),
      ],
    },
  },
  {
    // Pages compose components; styling lives in src/ui, layouts, features, and the workbench.
    files: ["src/pages/**/*.astro"],
    rules: {
      "no-restricted-syntax": [
        "error",
        ...restrictedSyntax,
        {
          selector:
            "JSXAttribute[name.name='class'], JSXAttribute > JSXNamespacedName[namespace.name='class']",
          message: "Pages compose components. Move styled markup into a component.",
        },
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
