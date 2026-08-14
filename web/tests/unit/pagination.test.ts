import { describe, expect, it } from "vitest";

import { isPaged, PAGE_PARAMS, readOffset, readPageParams } from "../../src/lib/pagination";

function params(search: string): URLSearchParams {
  return new URLSearchParams(search);
}

describe("page addresses read from a URL", () => {
  it("returns the first page when nothing addresses one", () => {
    expect(readPageParams(params(""))).toEqual({});
    expect(readPageParams(params("q=door"))).toEqual({});
    expect(isPaged({})).toBe(false);
  });

  it("reads each anchor kind", () => {
    expect(readPageParams(params("after_id=42"))).toEqual({ afterId: 42 });
    expect(readPageParams(params("before_id=42"))).toEqual({ beforeId: 42 });
    expect(readPageParams(params("offset=20"))).toEqual({ offset: 20 });
  });

  it("prefers an identifier anchor when a link carries more than one", () => {
    // The API rejects the combination, so a hand-edited URL resolves to the anchor rather than
    // failing the whole page render.
    expect(readPageParams(params("offset=20&after_id=42"))).toEqual({ afterId: 42 });
    expect(readPageParams(params("before_id=8&after_id=42"))).toEqual({ afterId: 42 });
  });

  it("ignores values that cannot address a page", () => {
    expect(readPageParams(params("after_id=0"))).toEqual({});
    expect(readPageParams(params("after_id=abc"))).toEqual({});
    expect(readPageParams(params("offset=-5"))).toEqual({});
    expect(readPageParams(params("offset=1.5"))).toEqual({});
    expect(readPageParams(params("offset=0"))).toEqual({});
  });

  it("reads the offset of an offset-only collection", () => {
    expect(readOffset(params("offset=20"))).toBe(20);
    expect(readOffset(params("offset=0"))).toBeUndefined();
    expect(readOffset(params("after_id=42"))).toBeUndefined();
  });

  it("treats any addressed page as unindexable", () => {
    expect(isPaged({ afterId: 42 })).toBe(true);
    expect(isPaged({ beforeId: 42 })).toBe(true);
    expect(isPaged({ offset: 20 })).toBe(true);
    expect(isPaged({ offset: 0 })).toBe(false);
  });

  it("names every parameter a canonical URL has to drop", () => {
    expect([...PAGE_PARAMS]).toEqual(["offset", "after_id", "before_id"]);
  });
});
