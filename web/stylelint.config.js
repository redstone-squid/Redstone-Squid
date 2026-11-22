/**
 * CSS rules for src/. Colors are defined once per theme in src/ui/themes.css, in OKLCH so
 * lightness and chroma can be adjusted perceptually; everything else uses the Tailwind
 * utilities that read those tokens.
 */
export default {
  plugins: ["stylelint-declaration-strict-value"],
  ignoreFiles: ["dist/**", "coverage/**", "playwright-report/**", "review-gallery/**"],
  overrides: [
    { files: ["**/*.svelte", "**/*.astro"], customSyntax: "postcss-html" },
    {
      files: ["src/ui/themes.css"],
      rules: { "scale-unlimited/declaration-strict-value": null },
    },
  ],
  rules: {
    "block-no-empty": true,
    "color-named": "never",
    "color-no-hex": true,
    "function-disallowed-list": [
      ["rgb", "rgba", "hsl", "hsla", "hwb", "lab", "lch", "color"],
      { message: "Write colors in oklch()." },
    ],
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
