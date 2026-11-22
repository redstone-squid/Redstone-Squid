import { describe, expect, it, vi } from "vitest";
import { createDraftFixture, validateDraft } from "@/workbench/draft-fixture";

describe("draft trial fixture", () => {
  it("rejects unknown versions and fractional, zero or negative door widths", () => {
    for (const width of ["", "0", "-1", "1.5", "text"]) {
      expect(validateDraft({ title: "Door", category: "door", width, version: "future" })).toEqual({
        width: "positive",
        version: "required",
      });
    }
  });
  it("stops upload progress and clears its pending timer on abort", async () => {
    vi.useFakeTimers();
    try {
      const api = createDraftFixture();
      const controller = new AbortController();
      const progress = vi.fn();
      const upload = api.upload(new File(["fixture"], "door.schem"), controller.signal, progress);
      const rejected = expect(upload).rejects.toMatchObject({ name: "AbortError" });
      controller.abort();
      await rejected;
      expect(progress).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
