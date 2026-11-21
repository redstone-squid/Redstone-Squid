import { describe, expect, it, vi } from "vitest";
import { createDraftFixture, validateDraft } from "../../src/features/workbench/draft-fixture";

describe("draft trial fixture", () => {
  it("rejects unknown versions and fractional, zero or negative door widths", () => {
    for (const width of ["", "0", "-1", "1.5", "text"]) {
      expect(validateDraft({ title: "Door", category: "door", width, version: "future" })).toEqual({
        width: "positive",
        version: "required",
      });
    }
  });
  it("clears pending transport timers when an operation aborts", async () => {
    vi.useFakeTimers();
    try {
      const api = createDraftFixture();
      const controller = new AbortController();
      const upload = api.upload(new File(["fixture"], "door.schem"), controller.signal, vi.fn());
      const rejected = expect(upload).rejects.toMatchObject({ name: "AbortError" });
      expect(vi.getTimerCount()).toBe(1);
      controller.abort();
      await rejected;
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
  it("does not progress or save after cancellation", async () => {
    const api = createDraftFixture();
    const controller = new AbortController();
    controller.abort();
    const progress = vi.fn();
    await expect(
      api.upload(new File([""], "door.schem"), controller.signal, progress),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(progress).not.toHaveBeenCalled();
  });
});
