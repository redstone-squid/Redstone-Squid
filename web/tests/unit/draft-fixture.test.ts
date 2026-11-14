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
  it("does not progress or save after cancellation", async () => {
    const api = createDraftFixture(false);
    const controller = new AbortController();
    controller.abort();
    const progress = vi.fn();
    await expect(
      api.upload(new File([""], "door.schem"), controller.signal, progress),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(progress).not.toHaveBeenCalled();
  });
});
