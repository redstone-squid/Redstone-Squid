import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { gzipSync } from "node:zlib";
import { evaluateBudgets, type BundleManifest, type EntryBudget } from "./bundle-budgets";

// Shared ceilings give interactive workflows room to grow and catch large regressions.
const interactiveBudget = { initialKiB: 96, completeKiB: 160 };

// Each workflow includes all of its independently hydrated islands. Keep this
// inventory in sync with page composition; locale routes share the same code.
const budgets: EntryBudget[] = [
  {
    // Loads on every page, so it must stay tiny.
    entries: [
      "src/layouts/DocumentHead.astro?astro&type=script&index=0&lang.ts",
      "src/layouts/DocumentHead.astro?astro&type=script&index=1&lang.ts",
    ],
    label: "Shared page scripts",
    initialKiB: 1,
    completeKiB: 1,
    renderer: false,
  },
  {
    entries: [
      "src/workbench/BrowsingPreview.svelte",
      "src/workbench/ButtonStory.svelte",
      "src/workbench/FieldStory.svelte",
      "src/workbench/LayoutStory.svelte",
      "src/workbench/ThemeToggle.svelte",
    ],
    deferredEntries: ["src/workbench/Controls.svelte"],
    label: "Design workbench (Svelte)",
    ...interactiveBudget,
  },
  {
    entries: ["src/workbench/DraftScenarioToggle.svelte", "src/workbench/DraftTrialStory.svelte"],
    label: "Svelte draft trial",
    ...interactiveBudget,
  },
];
const clientDirectory = path.resolve("dist/client");
const manifest = JSON.parse(
  await readFile(path.join(clientDirectory, ".vite/manifest.json"), "utf8"),
) as BundleManifest;
const assets = (await readdir(path.join(clientDirectory, "_astro"), { recursive: true }))
  .filter((file) => file.endsWith(".js"))
  .sort();
if (assets.length === 0)
  throw new Error("No emitted client JavaScript; run the production build first.");
const sizes = new Map(
  await Promise.all(
    assets.map(async (file): Promise<[string, number]> => {
      const name = `_astro/${file.replaceAll(path.sep, "/")}`;
      return [
        name,
        gzipSync(await readFile(path.join(clientDirectory, name)), { level: 9 }).byteLength,
      ];
    }),
  ),
);
const results = evaluateBudgets(
  manifest,
  sizes,
  budgets,
  "node_modules/@astrojs/svelte/dist/client.js",
);
const failures: string[] = [];
for (const result of results) {
  console.info(
    `${result.label}: initial ${(result.initialBytes / 1024).toFixed(1)} / ${result.initialKiB} KiB; complete ${(result.completeBytes / 1024).toFixed(1)} / ${result.completeKiB} KiB gzip`,
  );
  if (result.initialBytes > result.initialKiB * 1024)
    failures.push(`${result.label}: initial download budget exceeded`);
  if (result.completeBytes > result.completeKiB * 1024)
    failures.push(`${result.label}: complete workflow budget exceeded`);
}
const total = [...sizes.values()].reduce((sum, size) => sum + size, 0);
console.info(
  `All emitted JavaScript: ${(total / 1024).toFixed(1)} KiB gzip (informational; unrelated workflows are not combined).`,
);
if (failures.length > 0) throw new Error(failures.join("\n"));
