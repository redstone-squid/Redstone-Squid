import { existsSync, globSync, readFileSync, writeFileSync } from "node:fs";
import { format, resolveConfig } from "prettier";
import { SUPPORTED_LOCALES } from "../../src/locales/locale";
import { catalogContract, compareCatalogs, reviewStatus, sourceFingerprints } from "./catalogs";
import { compileCatalogs, functionName } from "./compile";
import { compileVocabulary, type Vocabulary } from "./vocabulary";

// Check the catalogs in src/locales/<locale>/messages.ftl, then compile them into
// src/locales/messages.gen.ts and the exported domain labels into src/locales/vocabulary.gen.ts.
//
//   (no flag)  fail if anything is invalid, unused, unreviewed, or out of date
//   --write    regenerate the compiled modules
//   --review   record that every translation matches the current English
const write = process.argv.includes("--write");
const review = process.argv.includes("--review");
const target = "src/locales/messages.gen.ts";
const catalogPath = (locale: string) => `src/locales/${locale}/messages.ftl`;
const revisionsPath = (locale: string) => `src/locales/${locale}/source-revisions.json`;

async function emit(path: string, source: string): Promise<void> {
  const formatted = await format(source, { ...(await resolveConfig(path)), filepath: path });
  if (write) writeFileSync(path, formatted);
  else if (readFileSync(path, "utf8") !== formatted)
    throw new Error(`Run bun run i18n:generate and commit ${path}.`);
}

const catalogs = Object.fromEntries(
  SUPPORTED_LOCALES.map((locale) => [locale, readFileSync(catalogPath(locale), "utf8")]),
);
const [english, ...translations] = SUPPORTED_LOCALES.map((locale) =>
  catalogContract(catalogs[locale] ?? ""),
);
if (!english) throw new Error("No locales are configured.");
for (const translation of translations) compareCatalogs(english, translation);

// A translation records the English fingerprint it was reviewed against, as in squid-i18n.
const fingerprints = sourceFingerprints(catalogs.en ?? "");
for (const locale of SUPPORTED_LOCALES.filter((locale) => locale !== "en")) {
  const path = revisionsPath(locale);
  if (review) {
    writeFileSync(path, `${JSON.stringify(fingerprints, null, 2)}\n`);
    continue;
  }
  const reviewed = existsSync(path)
    ? (JSON.parse(readFileSync(path, "utf8")) as Record<string, string>)
    : {};
  const { changed, obsolete } = reviewStatus(fingerprints, reviewed);
  if (changed.length > 0 || obsolete.length > 0)
    throw new Error(
      `English changed since the ${locale} translation was reviewed. Update ${catalogPath(locale)}, ` +
        `then run bun run i18n:review.\n${[...changed, ...obsolete].join("\n")}`,
    );
}

// Lint requires `import * as m from "@/locales/messages.gen"`, so calls always read `m.name`.
const called = new Set<string>();
for (const file of globSync("src/**/*.{ts,svelte,astro}")) {
  if (file.endsWith(".gen.ts")) continue;
  for (const match of readFileSync(file, "utf8").matchAll(/\bm\.([A-Za-z0-9]+)/g))
    if (match[1] !== undefined) called.add(match[1]);
}
const unused = Object.keys(english).filter((id) => !called.has(functionName(id)));
if (unused.length > 0)
  throw new Error(`Delete these messages or use them in src/:\n${unused.join("\n")}`);

await emit(target, compileCatalogs(catalogs));
const vocabulary = JSON.parse(readFileSync("../contracts/vocabulary.json", "utf8")) as Vocabulary;
await emit("src/locales/vocabulary.gen.ts", compileVocabulary(vocabulary, SUPPORTED_LOCALES));
console.info(
  `Compiled ${Object.keys(english).length} messages in ${SUPPORTED_LOCALES.length} locales.`,
);
