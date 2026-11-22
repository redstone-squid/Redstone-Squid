import { describe, expect, it } from "vitest";

import { localeFromPath, localizePath } from "@/locales/locale";

describe("locale mapping", () => {
  it("reads the locale only from a whole path prefix", () => {
    expect(localeFromPath("/zh-cn/builds")).toBe("zh-CN");
    expect(localeFromPath("/zh-cn")).toBe("zh-CN");
    expect(localeFromPath("/builds/zh-cn-example")).toBe("en");
  });

  it("preserves routes, queries, fragments, and page addresses while switching language", () => {
    expect(localizePath("/builds?q=door&after_id=42#results", "zh-CN")).toBe(
      "/zh-cn/builds?q=door&after_id=42#results",
    );
    expect(localizePath("/zh-cn/builds?q=door&offset=20", "en")).toBe("/builds?q=door&offset=20");
    expect(localizePath("/", "zh-CN")).toBe("/zh-cn");
    expect(localizePath("/zh-cn", "en")).toBe("/");
  });
});
