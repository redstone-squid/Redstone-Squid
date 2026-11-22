type BundleChunk = {
  file: string;
  isEntry?: boolean;
  imports?: string[];
  dynamicImports?: string[];
};
export type BundleManifest = Record<string, BundleChunk>;
export type EntryBudget = {
  entries: string[];
  /** Islands that hydrate on visibility and count toward the complete workflow. */
  deferredEntries?: string[];
  label: string;
  initialKiB: number;
  completeKiB: number;
  /** Svelte islands also load Astro's Svelte renderer; plain page scripts do not. */
  renderer?: boolean;
};
export type BudgetResult = EntryBudget & { initialBytes: number; completeBytes: number };

/** Follow manifest edges, including lazy code for the complete-workflow budget. */
export function dependencyFiles(
  manifest: BundleManifest,
  entries: string[],
  includeDynamic: boolean,
): Set<string> {
  const visited = new Set<string>();
  const files = new Set<string>();
  function visit(key: string): void {
    if (visited.has(key)) return;
    visited.add(key);
    const chunk = manifest[key];
    if (!chunk) throw new Error(`Missing bundle manifest entry: ${key}`);
    if (chunk.file.endsWith(".js")) files.add(chunk.file);
    for (const imported of chunk.imports ?? []) visit(imported);
    if (includeDynamic) for (const imported of chunk.dynamicImports ?? []) visit(imported);
  }
  entries.forEach(visit);
  return files;
}

/** Require every emitted entry and chunk to belong to an explicit budget. */
export function evaluateBudgets(
  manifest: BundleManifest,
  sizes: ReadonlyMap<string, number>,
  budgets: EntryBudget[],
  renderer: string,
): BudgetResult[] {
  const declared = new Set([
    renderer,
    ...budgets.flatMap(({ entries, deferredEntries = [] }) => [...entries, ...deferredEntries]),
  ]);
  for (const [key, chunk] of Object.entries(manifest)) {
    if (chunk.isEntry && chunk.file.endsWith(".js") && !declared.has(key)) {
      throw new Error(
        `Unbudgeted client entry: ${key}. Add its workflow to scripts/check-bundle.ts.`,
      );
    }
  }
  // Astro writes small page scripts into the HTML instead of emitting them, so they cost no
  // separate download; once one outgrows the inline limit it is emitted and measured.
  const inlined = new Set(
    Object.entries(manifest)
      .filter(([key, chunk]) => key.includes("?astro&type=script") && !sizes.has(chunk.file))
      .map(([, chunk]) => chunk.file),
  );
  const covered = new Set<string>();
  function bytes(files: Set<string>): number {
    let total = 0;
    for (const file of files) {
      if (inlined.has(file)) continue;
      const size = sizes.get(file);
      if (size === undefined) throw new Error(`Missing emitted JavaScript: ${file}`);
      total += size;
    }
    return total;
  }
  const results = budgets.map((budget) => {
    // Astro loads its Svelte renderer independently of each island's imports.
    const entries = budget.renderer === false ? budget.entries : [...budget.entries, renderer];
    const initial = dependencyFiles(manifest, entries, false);
    const complete = dependencyFiles(
      manifest,
      [...entries, ...(budget.deferredEntries ?? [])],
      true,
    );
    for (const file of complete) covered.add(file);
    return { ...budget, initialBytes: bytes(initial), completeBytes: bytes(complete) };
  });
  for (const file of sizes.keys()) {
    if (!covered.has(file)) throw new Error(`Emitted JavaScript is outside all budgets: ${file}`);
  }
  return results;
}
