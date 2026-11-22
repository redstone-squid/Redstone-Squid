import { describe, expect, it } from "vitest";

import {
  formatDate,
  formatNumber,
  localeFromPath,
  localizePath,
  translate,
  translateTaxonomy,
} from "../../src/lib/i18n";

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

describe("translations", () => {
  it("substitutes named variables without changing creator text", () => {
    expect(translate("en", "builds.byCreator", { name: "红石 Builder" })).toBe(
      "Builds by 红石 Builder",
    );
    expect(translate("zh-CN", "gallery.position", { current: 2, total: 4 })).toBe(
      "第 2 项，共 4 项",
    );
  });

  it("uses the English singular only for one record holder", () => {
    expect(translate("en", "record.holderCount", { count: 0 })).toBe("0 holders");
    expect(translate("en", "record.holderCount", { count: 1 })).toBe("1 holder");
    expect(translate("en", "record.holderCount", { count: 2 })).toBe("2 holders");
  });

  it("translates known taxonomy and falls back to the canonical API label", () => {
    expect(translateTaxonomy("zh-CN", "fastest_smallest")).toBe("先最快，再最小");
    expect(translateTaxonomy("zh-CN", "official_seamless", "Seamless")).toBe("无缝");
    expect(translateTaxonomy("zh-CN", "future_tag", "Future Tag")).toBe("Future Tag");
    expect(translateTaxonomy("en", "unlabelled")).toBe("unlabelled");
  });

  it("formats dates and numbers in the active locale", () => {
    expect(formatDate("en", "2026-08-10T23:30:00-02:00")).toBe("Aug 11, 2026");
    expect(formatDate("zh-CN", "2026-08-10T23:30:00-02:00")).toBe("2026年8月11日");
    expect(formatNumber("en", 12345)).toMatch(/12,345/);
  });
});
