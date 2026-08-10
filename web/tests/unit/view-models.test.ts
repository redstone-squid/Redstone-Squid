import { describe, expect, it } from "vitest";

import type { BuildSummary } from "../../src/generated/types.gen";
import {
  buildMedia,
  cardPreview,
  externalLinks,
  formatDimensions,
  formatTag,
  formatTiming,
  safeHttpsUrl,
} from "../../src/lib/view-models";

const summary: BuildSummary = {
  id: 1,
  revision: 1,
  title: "Compact Door",
  display_name: null,
  status: "confirmed",
  category: "door",
  dimensions: { width: 5, height: 6, depth: null },
  creators: ["Builder"],
  tags: [],
  preview: { kind: "render", url: "https://media.example/render.png" },
  version_spec: null,
  versions: ["Java 1.21"],
  opening_time: 8,
  closing_time: null,
  created_at: null,
  updated_at: null,
};

describe("safe media", () => {
  it.each([
    ["https://media.example/image.png", "https://media.example/image.png"],
    ["http://media.example/image.png", undefined],
    ["javascript:alert(1)", undefined],
    ["not a url", undefined],
  ])("validates %s", (value, expected) => {
    expect(safeHttpsUrl(value)).toBe(expected);
  });

  it("selects safe previews and media while preserving render-first order", () => {
    expect(cardPreview(summary)).toBe("https://media.example/render.png");
    expect(
      cardPreview({ ...summary, preview: { kind: "image", url: "http://unsafe.test/a" } }),
    ).toBeUndefined();
    expect(
      buildMedia({
        links: {
          renders: ["https://media.example/r1", "http://unsafe.test/r2"],
          images: ["https://media.example/i1"],
        },
      }),
    ).toEqual([
      { kind: "render", url: "https://media.example/r1", label: "Render 1" },
      { kind: "image", url: "https://media.example/i1", label: "Image 1" },
    ]);
    expect(externalLinks(["https://example.org/a", "ftp://example.org/b", "bad"])).toEqual([
      "https://example.org/a",
    ]);
  });
});

describe("technical formatting", () => {
  it("formats complete, partial, and absent dimensions", () => {
    expect(formatDimensions("en", { width: 5, height: 6, depth: 7 })).toBe("5 × 6 × 7");
    expect(formatDimensions("en", { width: 5, height: null, depth: 7 })).toBe("5 × ? × 7");
    expect(formatDimensions("zh-CN", { width: null, height: null, depth: null })).toBe("未记录");
  });

  it("formats timing and localized tags", () => {
    expect(formatTiming("en", 8)).toBe("8 game ticks");
    expect(formatTiming("zh-CN", null)).toBe("未记录");
    expect(
      formatTag("zh-CN", { key: "official_seamless", name: "Seamless", value: null, unit: null }),
    ).toBe("无缝");
    expect(formatTag("en", { key: "powered", name: "Powered", value: true, unit: null })).toBe(
      "Powered: Yes",
    );
    expect(formatTag("zh-CN", { key: "delay", name: "Delay", value: "4", unit: "gt" })).toBe(
      "Delay: 4 游戏刻",
    );
  });
});
