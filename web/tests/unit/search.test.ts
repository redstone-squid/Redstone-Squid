import { describe, expect, it } from "vitest";

import {
  composeGuidedQuery,
  creatorQuery,
  queryFromUrl,
  quoteQueryValue,
  readPositiveInteger,
} from "../../src/lib/search";

describe("guided query composition", () => {
  it("quotes and escapes user-authored field values", () => {
    expect(quoteQueryValue('Bob\\The "Builder"')).toBe('"Bob\\\\The \\"Builder\\""');
    expect(creatorQuery('Bob "Builder"')).toBe('creator:"Bob \\"Builder\\""');
  });

  it("composes every supported guided filter without a status clause", () => {
    const query = composeGuidedQuery({
      keywords: "fast door",
      creator: "Builder",
      version: "Java 1.21",
      category: "door",
      type: "flush",
      maxWidth: "5",
      maxHeight: "8",
      maxDepth: "12",
      maxOpening: "8gt",
      maxClosing: "0.5s",
      tag: "seamless",
    });
    expect(query).toBe(
      '"fast door" creator:"Builder" version:"Java 1.21" kind:"door" type:"flush" width<=5 height<=8 depth<=12 opening_time<=8gt closing_time<=0.5s tag:"seamless"',
    );
    expect(query).not.toContain("status");
  });

  it("omits empty and invalid numeric filters", () => {
    expect(composeGuidedQuery({ maxWidth: "5 blocks", maxHeight: "-1", creator: " " })).toBe("");
    expect(composeGuidedQuery({ maxWidth: "1.25", maxOpening: "2RT" })).toBe(
      "width<=1.25 opening_time<=2RT",
    );
  });
});

describe("query URL parsing", () => {
  it("reads shareable guided state", () => {
    const parameters = new URLSearchParams("keywords=door&creator=Builder&maxWidth=5&tag=");
    expect(queryFromUrl(parameters)).toMatchObject({
      keywords: "door",
      creator: "Builder",
      maxWidth: "5",
      tag: undefined,
    });
  });

  it.each([
    ["1", 1],
    ["42", 42],
    ["0", undefined],
    ["-1", undefined],
    ["1.5", undefined],
    [null, undefined],
    ["999999999999999999999", undefined],
  ])("parses positive identifiers from %s", (value, expected) => {
    expect(readPositiveInteger(value)).toBe(expected);
  });
});
