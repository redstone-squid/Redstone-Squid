/**
 * CSS rules for src/. Colors and shadows come from the tokens in src/ui/tokens.css, so a theme
 * change happens in one place and dark mode cannot miss a hard-coded value.
 */
export default {
  plugins: ["stylelint-declaration-strict-value"],
  ignoreFiles: ["dist/**", "coverage/**", "playwright-report/**", "review-gallery/**"],
  overrides: [
    { files: ["**/*.svelte", "**/*.astro"], customSyntax: "postcss-html" },
    {
      files: ["src/ui/tokens.css"],
      rules: { "color-no-hex": null, "scale-unlimited/declaration-strict-value": null },
    },
  ],
  rules: {
    "block-no-empty": true,
    "color-named": "never",
    "color-no-hex": true,
    "color-no-invalid-hex": true,
    "declaration-block-no-duplicate-properties": true,
    "declaration-no-important": true,
    "function-calc-no-unspaced-operator": true,
    "no-duplicate-selectors": true,
    "property-no-unknown": true,
    "selector-pseudo-class-no-unknown": [true, { ignorePseudoClasses: ["global"] }],
    "unit-no-unknown": true,
    "scale-unlimited/declaration-strict-value": [
      ["/color$/", "fill", "stroke"],
      {
        ignoreValues: [
          "currentColor",
          "transparent",
          "inherit",
          "initial",
          "unset",
          "none",
          "Canvas",
          "CanvasText",
          "ButtonText",
          "Highlight",
          "LinkText",
          "GrayText",
        ],
        disableFix: true,
      },
    ],
  },
};
