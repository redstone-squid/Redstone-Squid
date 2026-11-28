import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { gzipSync } from "node:zlib";
import { GROUPS } from "../src/workbench/groups";
import { evaluateBudgets, type BundleManifest, type EntryBudget } from "./bundle-budgets";

// Shared ceilings give interactive workflows room to grow and catch large regressions.
const interactiveBudget = { initialKiB: 96, completeKiB: 160 };
// A workbench page hydrates every story in its group as one island, with its localized
// fixtures, so it carries a folder's worth of components that no public page combines.
const workbenchBudget = { initialKiB: 144, completeKiB: 200 };

// Each workflow includes all of its independently hydrated islands. Keep this
// inventory in sync with page composition; locale routes share the same code.
const budgets: EntryBudget[] = [
  {
    // Loads on every page, so it must stay tiny: the document head, header search and footer appearance switch.
    entries: [
      "src/layouts/DocumentHead.astro?astro&type=script&index=0&lang.ts",
      "src/layouts/DocumentHead.astro?astro&type=script&index=1&lang.ts",
      "src/layouts/DocumentHead.astro?astro&type=script&index=2&lang.ts",
      "src/layouts/DocumentHead.astro?astro&type=script&index=3&lang.ts",
      "src/layouts/AppearanceSwitch.astro?astro&type=script&index=0&lang.ts",
      "src/layouts/HeaderSearch.astro?astro&type=script&index=0&lang.ts",
    ],
    label: "Shared page scripts",
    initialKiB: 1,
    completeKiB: 1,
    renderer: false,
  },
  // Each workbench page hydrates its group's stories as one island.
  ...Object.keys(GROUPS).map((slug) => ({
    entries: [
      `src/workbench/stories/${slug.charAt(0).toUpperCase() + slug.slice(1)}Stories.svelte`,
    ],
    label: `Design workbench: ${slug}`,
    ...workbenchBudget,
  })),
  {
    // Loads only when a visitor opens a schematic in 3D. Most of it is entity models embedded
    // in the renderer; Nucleation's WebAssembly (about 10 MB) comes on top and is not counted.
    entries: ["node_modules/schematic-renderer/dist/schematic-renderer.es.js"],
    label: "Schematic viewer, on demand",
    initialKiB: 2700,
    completeKiB: 3200,
    renderer: false,
  },
  {
    entries: ["src/workbench/DraftTrial.svelte"],
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
  .toSorted();
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
const total = sizes
  .values()
  .toArray()
  .reduce((sum, size) => sum + size, 0);
console.info(
  `All emitted JavaScript: ${(total / 1024).toFixed(1)} KiB gzip (informational; unrelated workflows are not combined).`,
);
if (failures.length > 0) throw new Error(failures.join("\n"));
