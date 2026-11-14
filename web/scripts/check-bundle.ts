import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { gzipSync } from "node:zlib";
import { evaluateBudgets, type BundleManifest, type EntryBudget } from "./bundle-budgets";

// Each workflow includes all of its independently hydrated islands. Keep this
// inventory in sync with page composition; locale routes share the same code.
const budgets: EntryBudget[] = [
  {
    entries: ["src/components/SearchComposer.tsx"],
    label: "Public search / catalogue",
    initialKiB: 100,
    completeKiB: 125,
  },
  {
    entries: ["src/components/MediaGallery.tsx"],
    label: "Public build detail",
    initialKiB: 100,
    completeKiB: 125,
  },
  {
    entries: ["src/components/SubmissionFlow.tsx"],
    label: "Submission editor",
    initialKiB: 110,
    completeKiB: 160,
  },
  {
    entries: ["src/components/CliLink.tsx"],
    label: "CLI linking",
    initialKiB: 110,
    completeKiB: 125,
  },
  {
    entries: ["src/components/MinecraftLink.tsx"],
    label: "Minecraft linking",
    initialKiB: 110,
    completeKiB: 125,
  },
  {
    entries: ["src/components/RuntimeSettings.tsx"],
    label: "Runtime settings",
    initialKiB: 110,
    completeKiB: 140,
  },
  {
    entries: ["src/features/workbench/Controls.tsx"],
    label: "React workbench reference",
    initialKiB: 110,
    completeKiB: 140,
  },
  {
    entries: ["src/features/workbench/Controls.svelte"],
    renderers: ["node_modules/@astrojs/svelte/dist/client.js"],
    label: "Design workbench (Svelte)",
    initialKiB: 65,
    completeKiB: 90,
  },
  {
    entries: ["src/features/workbench/DraftTrial.tsx"],
    label: "React draft trial",
    initialKiB: 110,
    completeKiB: 140,
  },
  {
    entries: ["src/features/workbench/DraftTrial.svelte"],
    renderers: ["node_modules/@astrojs/svelte/dist/client.js"],
    label: "Svelte draft trial",
    initialKiB: 50,
    completeKiB: 80,
  },
];
const clientDirectory = path.resolve("dist/client");
const manifest = JSON.parse(
  await readFile(path.join(clientDirectory, ".vite/manifest.json"), "utf8"),
) as BundleManifest;
const assets = (await readdir(path.join(clientDirectory, "_astro"), { recursive: true }))
  .filter((file) => file.endsWith(".js"))
  .sort();
if (!assets.length)
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
  "node_modules/@astrojs/react/dist/client.js",
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
if (failures.length) throw new Error(failures.join("\n"));
