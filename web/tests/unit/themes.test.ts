import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/ui/themes.css", "utf8");

// The declarations inside the first block that opens with `selector {`.
function declarations(selector: string): string[] {
  const start = css.indexOf(`${selector} {`);
  expect(start).toBeGreaterThan(-1);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  return body
    .split(";")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

describe("high contrast", () => {
  // A token missing from one block would only change with the device setting or the pin.
  it("swaps the same tokens whether the device asks for it or the visitor pins it", () => {
    expect(declarations(':root:not([data-contrast="standard"])')).toEqual(
      declarations(':root[data-contrast="more"]'),
    );
  });

  it("has a high-contrast value for every token it swaps", () => {
    for (const line of declarations(':root[data-contrast="more"]')) {
      const [token, value] = line.split(": ");
      expect(value).toBe(`var(${token ?? ""}-more)`);
      expect(css).toContain(`${token ?? ""}-more: light-dark(`);
    }
  });
});
