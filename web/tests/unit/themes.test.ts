import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { THEMES } from "@/ui/themes";

// Each `:root[data-theme="name"]` block in themes.css, with the custom properties it sets.
const themes = new Map(
  readFileSync("src/ui/themes.css", "utf8")
    .matchAll(/\[data-theme="([a-z-]+)"\]\s*\{([^}]*)\}/g)
    .toArray()
    .map(([, name, body]): [string, string[]] => [
      name ?? "",
      (body ?? "")
        .matchAll(/(--[a-z0-9-]+):/g)
        .map(([, token]) => token ?? "")
        .toArray()
        .toSorted((a, b) => a.localeCompare(b)),
    ]),
);

describe("themes", () => {
  it("defines a block for every listed theme and no others", () => {
    expect(
      themes
        .keys()
        .toArray()
        .toSorted((a, b) => a.localeCompare(b)),
    ).toEqual([...THEMES].toSorted((a, b) => a.localeCompare(b)));
  });

  it("sets the same tokens in every theme, so none falls back to another theme's colors", () => {
    const [first, ...rest] = themes.values().toArray();
    for (const tokens of rest) expect(tokens).toEqual(first);
  });
});
