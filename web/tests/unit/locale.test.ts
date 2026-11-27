import { describe, expect, it } from "vitest";

import { formatList } from "@/locales/format";
import { localeFromPath, localizePath } from "@/locales/locale";

describe("locale mapping", () => {
  it("reads the locale only from a whole path prefix", () => {
    expect(localeFromPath("/zh-cn/builds")).toBe("zh-CN");
    expect(localeFromPath("/zh-cn")).toBe("zh-CN");
    expect(localeFromPath("/builds/zh-cn-example")).toBeUndefined();
    expect(localeFromPath("/")).toBeUndefined();
  });

  it("preserves routes, queries, fragments, and page addresses while switching language", () => {
    expect(localizePath("/builds?q=door&cursor=abc#results", "zh-CN")).toBe(
      "/zh-cn/builds?q=door&cursor=abc#results",
    );
    expect(localizePath("/zh-cn/builds?q=door&offset=20", "en")).toBe("/builds?q=door&offset=20");
    expect(localizePath("/", "zh-CN")).toBe("/zh-cn");
    expect(localizePath("/zh-cn", "en")).toBe("/");
  });

  it("joins names the way each locale writes a list", () => {
    expect(formatList(["CircuitSage", "红石猫", "Space Builder"], "en")).toBe(
      "CircuitSage, 红石猫, and Space Builder",
    );
    expect(formatList(["CircuitSage", "红石猫"], "zh-CN")).toBe("CircuitSage和红石猫");
  });
});
