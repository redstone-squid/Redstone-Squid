import { describe, expect, it } from "vitest";
import { pageHref, readOffset, readPageAddress } from "@/features/catalogue/pagination";

const params = (search: string) => new URLSearchParams(search);

describe("page addresses", () => {
  it("reads one address, preferring identifiers over an offset", () => {
    expect(readPageAddress(params(""))).toEqual({});
    expect(readPageAddress(params("after_id=42"))).toEqual({ after_id: 42 });
    expect(readPageAddress(params("before_id=8"))).toEqual({ before_id: 8 });
    expect(readPageAddress(params("offset=20&after_id=42"))).toEqual({ after_id: 42 });
  });

  it.each(["0", "-1", "1.5", "abc", "9007199254740993"])(
    "skips the invalid value %s and falls back to the next address",
    (value) => {
      expect(readPageAddress(params(`after_id=${value}&offset=20`))).toEqual({ offset: 20 });
      expect(readOffset(params(`offset=${value}`))).toBeUndefined();
    },
  );

  it("links to an anchor, replacing the current address and keeping other parameters", () => {
    const url = new URL("https://catalogue.test/zh-cn/records?sort=new&after_id=3");
    expect(pageHref(url, { offset: null, after_id: null, before_id: 9 })).toBe(
      "/zh-cn/records?sort=new&before_id=9",
    );
  });
});
