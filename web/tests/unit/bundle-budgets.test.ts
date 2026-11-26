import { describe, expect, it } from "vitest";
import {
  dependencyFiles,
  evaluateBudgets,
  type BundleManifest,
  type EntryBudget,
} from "../../scripts/bundle-budgets";

const manifest = {
  island: { file: "island.js", isEntry: true, imports: ["shared"], dynamicImports: ["lazy"] },
  renderer: { file: "renderer.js", isEntry: true, imports: ["shared"] },
  shared: { file: "shared.js", imports: ["island"] },
  lazy: { file: "lazy.js", imports: ["shared"] },
} satisfies BundleManifest;
const sizes = new Map([
  ["island.js", 10],
  ["renderer.js", 20],
  ["shared.js", 30],
  ["lazy.js", 40],
]);
const budgets: [EntryBudget] = [
  { entries: ["island"], label: "Example workflow", initialKiB: 1, completeKiB: 2 },
];

describe("workflow bundle budgets", () => {
  it("includes the separately loaded renderer, deduplicates shared imports, and budgets lazy code", () => {
    expect(evaluateBudgets(manifest, sizes, budgets, "renderer")).toEqual([
      { ...budgets[0], initialBytes: 60, completeBytes: 100 },
    ]);
    expect([...dependencyFiles(manifest, ["island"], false)]).toEqual(["island.js", "shared.js"]);
  });
  it("requires explicit coverage for new entry points", () => {
    expect(() =>
      evaluateBudgets(
        { ...manifest, newIsland: { file: "new.js", isEntry: true } },
        sizes,
        budgets,
        "renderer",
      ),
    ).toThrow("Unbudgeted client entry");
  });
  it("counts visibility-triggered entries only in the complete workflow", () => {
    const deferred = { ...budgets[0], deferredEntries: ["lazy"] };
    const withDeferredEntry = {
      ...manifest,
      island: { ...manifest.island, dynamicImports: [] },
      lazy: { ...manifest.lazy, isEntry: true },
    };
    expect(evaluateBudgets(withDeferredEntry, sizes, [deferred], "renderer")).toEqual([
      { ...deferred, initialBytes: 60, completeBytes: 100 },
    ]);
  });
  it("measures lazy code with its own budget, not every workflow that can load it", () => {
    const viewer = {
      entries: ["lazy"],
      label: "On demand",
      initialKiB: 1,
      completeKiB: 1,
      renderer: false,
    };
    expect(evaluateBudgets(manifest, sizes, [budgets[0], viewer], "renderer")).toEqual([
      { ...budgets[0], initialBytes: 60, completeBytes: 60 },
      { ...viewer, initialBytes: 80, completeBytes: 80 },
    ]);
  });
  it("rejects stale manifests, missing imports, and unaccounted emitted chunks", () => {
    expect(() => dependencyFiles(manifest, ["missing"], true)).toThrow(
      "Missing bundle manifest entry",
    );
    expect(() => evaluateBudgets(manifest, new Map(), budgets, "renderer")).toThrow(
      "Missing emitted JavaScript",
    );
    expect(() =>
      evaluateBudgets(manifest, new Map([...sizes, ["orphan.js", 10]]), budgets, "renderer"),
    ).toThrow("outside all budgets");
  });
});
