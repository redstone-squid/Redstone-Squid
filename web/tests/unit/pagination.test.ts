import { describe, expect, it } from "vitest";
import { pageHref, readPageAddress } from "@/server/pagination";

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
    },
  );

  it("links to an anchor, replacing the current address and keeping other parameters", () => {
    const url = new URL("https://catalogue.test/records?sort=new&after_id=3");
    expect(pageHref(url, { offset: null, after_id: null, before_id: 9 })).toBe(
      "/records?sort=new&before_id=9",
    );
  });

  // Middleware rewrites /zh-cn/records to /records, so pages see the unprefixed URL.
  it("keeps the page's locale", () => {
    document.documentElement.lang = "zh-CN";
    const url = new URL("https://catalogue.test/records?after_id=3");
    expect(pageHref(url, { offset: null, after_id: 4, before_id: null })).toBe(
      "/zh-cn/records?after_id=4",
    );
  });
});
