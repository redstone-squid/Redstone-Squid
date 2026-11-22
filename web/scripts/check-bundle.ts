import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { gzipSync } from "node:zlib";
import { evaluateBudgets, type BundleManifest, type EntryBudget } from "./bundle-budgets";

// Each workflow includes all of its independently hydrated islands. Keep this
// inventory in sync with page composition; locale routes share the same code.
const budgets: EntryBudget[] = [
  {
    entries: ["src/workbench/Controls.svelte"],
    label: "Design workbench (Svelte)",
    initialKiB: 47,
    completeKiB: 60,
  },
  {
    entries: ["src/workbench/DraftTrial.svelte"],
    label: "Svelte draft trial",
    initialKiB: 30,
    completeKiB: 45,
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
