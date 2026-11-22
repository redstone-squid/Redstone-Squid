import { globSync, readFileSync, writeFileSync } from "node:fs";
import { format, resolveConfig } from "prettier";
import { SUPPORTED_LOCALES } from "../../src/locales/locale";
import { catalogContract, compareCatalogs } from "./catalogs";
import { compileCatalogs, functionName } from "./compile";

// Validate the catalogs, then compile them into src/locales/messages.ts. With --write the
// module is regenerated; otherwise the build fails if the committed module is out of date.
// Every message must be called somewhere in src/, so deleted features cannot leave text behind.
const target = "src/locales/messages.ts";
const catalogs = Object.fromEntries(
  SUPPORTED_LOCALES.map((locale) => [locale, readFileSync(`src/locales/${locale}.ftl`, "utf8")]),
);
const [reference, ...translations] = SUPPORTED_LOCALES.map((locale) =>
  catalogContract(catalogs[locale] ?? ""),
);
if (!reference) throw new Error("No locales are configured.");
for (const translation of translations) compareCatalogs(reference, translation);

// Lint requires `import * as m from ".../locales/messages"`, so calls always read `m.name`.
const called = new Set<string>();
for (const file of globSync("src/**/*.{ts,svelte,astro}")) {
  if (file === target) continue;
  for (const match of readFileSync(file, "utf8").matchAll(/\bm\.([A-Za-z0-9]+)/g))
    if (match[1] !== undefined) called.add(match[1]);
}
const unused = Object.keys(reference).filter((id) => !called.has(functionName(id)));
if (unused.length > 0)
  throw new Error(`Delete these messages or use them in src/:\n${unused.join("\n")}`);

const generated = await format(compileCatalogs(catalogs), {
  ...(await resolveConfig(target)),
  filepath: target,
});
if (process.argv.includes("--write")) writeFileSync(target, generated);
else if (readFileSync(target, "utf8") !== generated)
  throw new Error("Run bun run i18n:generate and commit src/locales/messages.ts.");
console.info(
  `Compiled ${Object.keys(reference).length} messages in ${SUPPORTED_LOCALES.length} locales.`,
);
