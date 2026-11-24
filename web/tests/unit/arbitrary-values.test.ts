import { describe, expect, it } from "vitest";
import { arbitraryValue, uiArbitraryValue } from "../../scripts/lint/arbitrary-values";

const restricted = (pattern: { pattern: string }, name: string) =>
  new RegExp(pattern.pattern).test(name);

describe("arbitrary Tailwind values", () => {
  it.each(["grid-cols-[auto_auto]", "aria-[current=page]:font-semibold", "w-[13rem]"])(
    "rejects %s outside src/ui",
    (name) => {
      expect(restricted(arbitraryValue, name)).toBe(true);
    },
  );

  it.each([
    "@max-lg:grid-cols-[1fr_auto]",
    "motion-safe:transition-[opacity,translate]",
    "[&_code]:text-[0.9em]",
    "align-[-0.1em]",
    "max-h-[calc(100dvh-2rem)]",
    "max-w-[min(22rem,calc(100vw-2rem))]",
    "aria-[current=page]:font-semibold",
    "has-[[data-card-link]:focus-visible]:outline-3",
    "[&_.lc-layout-svg]:h-auto!",
    "grid-cols-fill-card",
  ])("allows %s inside src/ui", (name) => {
    expect(restricted(uiArbitraryValue, name)).toBe(false);
  });

  it.each(["start-[0.4375rem]", "size-[18px]", "hover:w-[13rem]", "[mask-type:alpha]", "z-[5]!"])(
    "rejects %s inside src/ui",
    (name) => {
      expect(restricted(uiArbitraryValue, name)).toBe(true);
    },
  );
});
