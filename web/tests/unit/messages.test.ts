import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import {
  catalogContract,
  compareCatalogs,
  reviewStatus,
  sourceFingerprints,
} from "../../scripts/i18n/catalogs";
import { compileCatalogs } from "../../scripts/i18n/compile";
import { compileVocabulary } from "../../scripts/i18n/vocabulary";

type Message = (...args: unknown[]) => string;

/** Compile catalogs and import the result, so tests exercise the generated code itself. */
async function load(
  catalogs: Record<string, string>,
  pseudoLocales: string[] = [],
): Promise<Record<string, Message>> {
  const format = path.resolve("src/locales/format.ts");
  const locale = path.resolve("src/locales/locale.ts");
  const javascript = ts
    .transpileModule(compileCatalogs(catalogs, pseudoLocales), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    })
    .outputText.replace('"./format"', JSON.stringify(format))
    .replace('"./locale"', JSON.stringify(locale));
  // Vite only imports modules inside the project, so the output goes to the ignored cache.
  const cache = path.resolve("node_modules/.cache");
  mkdirSync(cache, { recursive: true });
  const file = path.join(mkdtempSync(path.join(cache, "messages-")), "messages.ts");
  writeFileSync(file, javascript);
  return (await import(/* @vite-ignore */ file)) as Record<string, Message>;
}

describe("message compiler", () => {
  it("compiles each message into a function of the locale", async () => {
    const messages = await load({
      en: "nav-skip = Skip to content",
      "zh-CN": "nav-skip = 跳至内容",
    });
    expect(messages.navSkip?.("en")).toBe("Skip to content");
    expect(messages.navSkip?.("zh-CN")).toBe("跳至内容");
  });

  it("defaults to the page locale from <html lang>", async () => {
    const messages = await load({
      en: "nav-skip = Skip to content",
      "zh-CN": "nav-skip = 跳至内容",
    });
    document.documentElement.lang = "zh-CN";
    expect(messages.navSkip?.()).toBe("跳至内容");
  });

  it("selects plural variants by category, after exact numeric matches", async () => {
    const { buildCount } = await load({
      en: "build-count = { $count ->\n  [0] No builds\n  [one] { $count } build\n *[other] { $count } builds\n}",
    });
    expect(buildCount?.({ count: 0 }, "en")).toBe("No builds");
    expect(buildCount?.({ count: 1 }, "en")).toBe("1 build");
    expect(buildCount?.({ count: 1200 }, "en")).toBe("1,200 builds");
  });

  it("formats numbers and dates with the options the message names", async () => {
    const { checked, share } = await load({
      en: [
        'checked = Checked { DATETIME($date, dateStyle: "medium") }',
        'share = { NUMBER($ratio, style: "percent") }',
      ].join("\n"),
      "zh-CN": [
        'checked = 检查于 { DATETIME($date, dateStyle: "medium") }',
        'share = { NUMBER($ratio, style: "percent") }',
      ].join("\n"),
    });
    // Dates render in UTC, so the evening in UTC-2 is the next day.
    expect(checked?.({ date: "2026-08-10T23:30:00-02:00" }, "en")).toBe("Checked Aug 11, 2026");
    expect(checked?.({ date: "2026-08-10T23:30:00-02:00" }, "zh-CN")).toBe("检查于 2026年8月11日");
    expect(share?.({ ratio: 0.25 }, "en")).toBe("25%");
  });

  it("inlines terms and calls referenced messages", async () => {
    const { greeting } = await load({
      en: "-brand = Squid\nname = { $user }\ngreeting = { -brand } welcomes { name }",
    });
    expect(greeting?.({ user: "Alex" }, "en")).toBe("Squid welcomes Alex");
  });

  it("keeps user text that looks like Fluent or markup literal", async () => {
    const { byCreator } = await load({ en: "by-creator = Builds by { $name }" });
    expect(byCreator?.({ name: "{ $name } <b>builder</b>" }, "en")).toBe(
      "Builds by { $name } <b>builder</b>",
    );
  });

  it("compiles pseudo-locales as accented, bracketed, longer English", async () => {
    const { holders } = await load(
      { en: "holders = { $count ->\n  [one] { $count } holder\n *[other] { $count } holders\n}" },
      ["en-XA"],
    );
    expect(holders?.({ count: 2 }, "en-XA")).toBe("[2 ĥöļđéŕš····]");
  });

  it("types arguments from how every locale uses them", () => {
    const source = compileCatalogs({
      en: "count = { $count } holders",
      "zh-CN": "count = { $count ->\n  *[other] { $count } 位\n}",
    });
    expect(source).toContain(
      "export function count(args: { count: number }, locale: Locale = getLocale()): string",
    );
  });

  it("rejects constructs it cannot compile", () => {
    expect(() => compileCatalogs({ en: "menu =\n  .label = Menu" })).toThrow("attributes");
    expect(() => compileCatalogs({ en: "build-a = One\nbuildA = Two" })).toThrow("both compile");
    expect(() => compileCatalogs({ en: "size = { UPPER($x) }" })).toThrow("Unsupported");
  });
});

describe("catalog contract", () => {
  it("requires matching message IDs and variables in every locale", () => {
    expect(() => compareCatalogs(catalogContract("hello = Hello"), {})).toThrow("must match");
    expect(() =>
      compareCatalogs(catalogContract("hello = { $name }"), catalogContract("hello = { $other }")),
    ).toThrow("must match");
  });

  it("rejects malformed, duplicate, cyclic, and non-kebab-case messages", () => {
    expect(() => catalogContract("invalid message")).toThrow("Invalid Fluent");
    expect(() => catalogContract("hello = Hello\nhello = Again")).toThrow("Duplicate");
    expect(() => catalogContract("hello = { missing }")).toThrow("Unknown Fluent reference");
    expect(() => catalogContract("a = { b }\nb = { a }")).toThrow("Cyclic Fluent reference");
    expect(() => catalogContract("helloWorld = Hi")).toThrow("kebab-case");
  });

  it("collects variables through message references but not term arguments", () => {
    expect(catalogContract("name = { $user }\ngreeting = Hello { name }").greeting).toEqual([
      "user",
    ]);
    expect(catalogContract("-brand = Squid\nhello = { -brand }")).toEqual({ hello: [] });
  });
});

describe("translation review", () => {
  it("asks for review when English wording changes, not when only whitespace does", () => {
    const reviewed = sourceFingerprints("nav-skip = Skip to content\n-brand = Squid");
    expect(
      reviewStatus(sourceFingerprints("nav-skip   =   Skip to content\n-brand = Squid"), reviewed),
    ).toEqual({
      changed: [],
      obsolete: [],
    });
    expect(reviewStatus(sourceFingerprints("nav-skip = Skip to main content"), reviewed)).toEqual({
      changed: ["nav-skip"],
      obsolete: ["-brand"],
    });
  });
});

describe("vocabulary compiler", () => {
  const labels = { first: "First", fastest: "Fastest" };

  it("types each group's values with its OpenAPI type", () => {
    const source = compileVocabulary(
      {
        en: { record_criterion: labels },
        "zh-CN": { record_criterion: { first: "首个", fastest: "最快" } },
      },
      ["en", "zh-CN"],
    );
    expect(source).toContain(
      "export function recordCriterionLabel(value: RecordCriterion, locale: Locale = getLocale()): string",
    );
    expect(source).toContain("satisfies Record<Locale, Record<RecordCriterion, string>>");
  });

  it("rejects locales that label different values and groups without a type", () => {
    expect(() =>
      compileVocabulary(
        { en: { record_criterion: labels }, "zh-CN": { record_criterion: { first: "首个" } } },
        ["en", "zh-CN"],
      ),
    ).toThrow("must label exactly");
    expect(() => compileVocabulary({ en: { mystery: labels } }, ["en"])).toThrow("OpenAPI type");
  });
});
