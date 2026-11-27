import { describe, expect, it } from "vitest";
import { pageHref, readPageAddress } from "@/server/pagination";

const params = (search: string) => new URLSearchParams(search);

describe("page addresses", () => {
  it("reads one address, preferring a cursor over an offset", () => {
    expect(readPageAddress(params(""))).toEqual({});
    expect(readPageAddress(params("cursor=abc"))).toEqual({ cursor: "abc" });
    expect(readPageAddress(params("offset=20&cursor=abc"))).toEqual({ cursor: "abc" });
    expect(readPageAddress(params("offset=20"))).toEqual({ offset: 20 });
  });

  it.each(["0", "-1", "1.5", "abc", "9007199254740993"])("skips the invalid offset %s", (value) => {
    expect(readPageAddress(params(`offset=${value}&cursor=`))).toEqual({});
  });

  it("links to an anchor, replacing the current address and keeping other parameters", () => {
    const url = new URL("https://catalogue.test/records?tag=a&tag=b&cursor=old&offset=5");
    expect(pageHref(url, { offset: null, cursor: "new" })).toBe("/records?tag=a&tag=b&cursor=new");
  });

  // Middleware rewrites /zh-cn/records to /records, so pages see the unprefixed URL.
  it("keeps the page's locale", () => {
    document.documentElement.lang = "zh-CN";
    const url = new URL("https://catalogue.test/records?cursor=old");
    expect(pageHref(url, { offset: 20, cursor: null })).toBe("/zh-cn/records?offset=20");
  });
});
