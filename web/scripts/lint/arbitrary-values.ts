/**
 * `better-tailwindcss/no-restricted-classes` patterns for arbitrary Tailwind values. Each
 * pattern is matched against one class, variants included.
 */

/** Outside src/ui: any arbitrary value, property, or variant. */
export const arbitraryValue = {
  pattern: String.raw`\[`,
  message:
    "Arbitrary Tailwind values belong in src/ui. Compose a ui component or use a theme utility.",
};

// Values the theme cannot name: grid track lists, transition property lists, sizes relative to
// the text (em), and sizes bounded by the viewport (vh, vw, calc). Arbitrary variants such as
// `aria-[current=page]:` and `[&_code]:` select elements rather than set values, so they pass.
const allowed = [
  String.raw`(?!(?:.*:)?(?:grid-cols|grid-rows|transition)-\[)`,
  String.raw`(?!.*-\[-?[\d.]+em\]!?$)`,
  String.raw`(?!.*-\[[^\]]*(?:vh|vw|calc\()[^\]]*\]!?$)`,
].join("");

/** Inside src/ui: an arbitrary value or property outside the allowlist above. */
export const uiArbitraryValue = {
  pattern: String.raw`^${allowed}(?:.*-\[[^\]]*\]|(?:.*:)?\[[^\]]*\])!?$`,
  message:
    "Use a theme token or an app.css utility. src/ui allows arbitrary values only for grid tracks, transition properties, em sizes, and viewport-bounded sizes.",
};
