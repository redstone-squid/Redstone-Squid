import { describe, expect, it } from "vitest";

import {
  apiLocaleToRoute,
  dictionaries,
  formatDate,
  formatNumber,
  localeFromPath,
  localizePath,
  routeLocaleToApi,
  translate,
  translateTaxonomy,
} from "../../src/lib/i18n";

describe("locale mapping", () => {
  it("keeps route and API spellings explicit", () => {
    expect(routeLocaleToApi("en")).toBe("en");
    expect(routeLocaleToApi("zh-cn")).toBe("zh-CN");
    expect(apiLocaleToRoute("en")).toBe("en");
    expect(apiLocaleToRoute("zh-CN")).toBe("zh-cn");
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

describe("translations", () => {
  it("requires both dictionaries to expose the same complete key set", () => {
    expect(Object.keys(dictionaries["zh-CN"]).sort()).toEqual(Object.keys(dictionaries.en).sort());
    expect(Object.values(dictionaries.en).every(Boolean)).toBe(true);
    expect(Object.values(dictionaries["zh-CN"]).every(Boolean)).toBe(true);
  });

  it("substitutes named variables without changing creator text", () => {
    expect(translate("en", "builds.byCreator", { name: "红石 Builder" })).toBe(
      "Builds by 红石 Builder",
    );
    expect(translate("zh-CN", "gallery.position", { current: 2, total: 4 })).toBe(
      "第 2 项，共 4 项",
    );
  });

  it("translates known taxonomy and falls back to the canonical API label", () => {
    expect(translateTaxonomy("zh-CN", "fastest_smallest")).toBe("先最快，再最小");
    expect(translateTaxonomy("zh-CN", "official_seamless", "Seamless")).toBe("无缝");
    expect(translateTaxonomy("zh-CN", "future_tag", "Future Tag")).toBe("Future Tag");
    expect(translateTaxonomy("en", "unlabelled")).toBe("unlabelled");
  });

  it("formats dates and numbers in the active locale", () => {
    expect(formatDate("en", "2026-08-10T12:00:00Z")).toContain("2026");
    expect(formatDate("zh-CN", "2026-08-10T12:00:00Z")).toContain("2026");
    expect(formatNumber("en", 12345)).toMatch(/12,345/);
  });
});
