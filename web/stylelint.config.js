/**
 * CSS rules for src/, including Svelte `<style>` blocks. Colors are defined once per theme in
 * src/ui/themes.css, in OKLCH so lightness and chroma can be adjusted perceptually; everything
 * else reads those tokens through Tailwind utilities or `var(--color-*)`.
 */
const legacyColorFunctions = ["rgb", "rgba", "hsl", "hsla", "hwb", "lab", "lch", "color"];

export default {
  plugins: ["stylelint-declaration-strict-value"],
  ignoreFiles: ["dist/**", "coverage/**", "playwright-report/**", "review-gallery/**"],
  overrides: [
    { files: ["**/*.svelte", "**/*.astro"], customSyntax: "postcss-html" },
    {
      files: ["src/ui/themes.css"],
      rules: {
        "scale-unlimited/declaration-strict-value": null,
        "function-disallowed-list": [legacyColorFunctions, { message: "Write colors in oklch()." }],
      },
    },
  ],
  rules: {
    "block-no-empty": true,
    "color-named": "never",
    "color-no-hex": true,
    "function-disallowed-list": [
      [...legacyColorFunctions, "oklch", "light-dark"],
      { message: "Use a color token such as var(--color-fg); src/ui/themes.css defines them." },
    ],
    "color-no-invalid-hex": true,
    "declaration-block-no-duplicate-properties": true,
    "declaration-no-important": true,
    // Stacking and motion come from the tokens in src/ui/app.css.
    "declaration-property-value-allowed-list": [
      { "z-index": ["auto", "/^var\\(--z-[a-z]+\\)$/"] },
      { message: "Use a z-index from the ladder in src/ui/app.css, such as var(--z-sticky)." },
    ],
    "declaration-property-unit-disallowed-list": [
      { "/^(?:transition|animation)/": ["s", "ms"] },
      { message: "Use a duration token such as var(--duration-fast)." },
    ],
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
          "HighlightText",
          "LinkText",
          "GrayText",
        ],
        disableFix: true,
      },
    ],
  },
};
