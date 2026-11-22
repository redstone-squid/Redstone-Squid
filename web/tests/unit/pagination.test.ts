import { describe, expect, it } from "vitest";
import { readOffset } from "@/features/catalogue/pagination";

describe("offset pagination", () => {
  it("reads a safe positive offset", () => {
    expect(readOffset(new URLSearchParams("offset=20"))).toBe(20);
    expect(readOffset(new URLSearchParams("offset=9007199254740991"))).toBe(
      Number.MAX_SAFE_INTEGER,
    );
  });

  it("uses the first page when no offset is supplied", () => {
    expect(readOffset(new URLSearchParams("after_id=42"))).toBeUndefined();
  });

  it.each(["0", "-1", "1.5", "abc", "9007199254740993"])(
    "uses the first page for offset %s",
    (offset) => {
      expect(readOffset(new URLSearchParams({ offset }))).toBeUndefined();
    },
  );
});
