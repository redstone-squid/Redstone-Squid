import { describe, expect, it } from "vitest";
import { catalogContract, compareCatalogs } from "../../scripts/catalogs";
import english from "../../src/locales/en.ftl?raw";
import chinese from "../../src/locales/zh-CN.ftl?raw";
import { translate } from "../../src/lib/i18n";

describe("Fluent catalogs", () => {
  it("keeps every translated message and variable in sync", () => {
    expect(() => compareCatalogs(catalogContract(english), catalogContract(chinese))).not.toThrow();
  });
  it("rejects malformed, duplicate, missing, and mismatched messages", () => {
    expect(() => catalogContract("invalid message")).toThrow("Invalid Fluent");
    expect(() => catalogContract("hello = Hello\nhello = Again")).toThrow("Duplicate");
    expect(() => compareCatalogs(catalogContract("hello = Hello"), {})).toThrow("must match");
    expect(() =>
      compareCatalogs(catalogContract("hello = { $name }"), catalogContract("hello = { $other }")),
    ).toThrow("must match");
  });
  it("resolves reference variables and rejects unknown or cyclic references", () => {
    expect(catalogContract("name = { $user }\ngreeting = Hello { name }").greeting).toEqual([
      "user",
    ]);
    expect(() => catalogContract("hello = { missing }")).toThrow("Unknown Fluent reference");
    expect(() => catalogContract("a = { b }\nb = { a }")).toThrow("Cyclic Fluent reference");
    expect(catalogContract("-brand = Squid\nhello = { -brand }")).toEqual({ hello: [] });
  });
  it("lets translators own plural rules and variable placement", () => {
    expect(translate("en", "workbench.build-count", { count: 1 })).toBe("One example build");
    expect(translate("en", "workbench.build-count", { count: 3 })).toBe("3 example builds");
    expect(translate("zh-CN", "workbench.build-count", { count: 3 })).toBe("3 个示例作品");
  });
  it("reports missing interpolation arguments rather than displaying broken syntax", () => {
    // @ts-expect-error Exercise untyped callers that omit required variables.
    expect(() => translate("en", "builds.byCreator")).toThrow("Unable to format translation");
  });
  it("preserves user text containing Fluent syntax", () => {
    expect(translate("en", "builds.byCreator", { name: "{ $name } <b>builder</b>" })).toBe(
      "Builds by { $name } <b>builder</b>",
    );
  });
});
