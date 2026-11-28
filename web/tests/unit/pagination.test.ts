import { describe, expect, it } from "vitest";
import { pageHref, readPageAddress } from "@/server/pagination";

const params = (search: string) => new URLSearchParams(search);

describe("page addresses", () => {
  it("requests offset mode even on the first page", () => {
    expect(readPageAddress(params(""))).toEqual({ offset: 0, page_size: 20 });
    expect(readPageAddress(params("page=3")).offset).toBe(40);
    expect(readPageAddress(params("page=502")).offset).toBe(0);
  });

  it("links to an anchor, replacing the current address and keeping other parameters", () => {
    const url = new URL("https://catalogue.test/records?tag=a&tag=b&cursor=old&offset=5");
    expect(pageHref(url, { offset: 20, cursor: null })).toBe("/records?tag=a&tag=b&page=2");
  });

  // Middleware rewrites /zh-cn/records to /records, so pages see the unprefixed URL.
  it("keeps the page's locale", () => {
    document.documentElement.lang = "zh-CN";
    const url = new URL("https://catalogue.test/records?cursor=old");
    expect(pageHref(url, { offset: 20, cursor: null })).toBe("/zh-cn/records?page=2");
  });
});
