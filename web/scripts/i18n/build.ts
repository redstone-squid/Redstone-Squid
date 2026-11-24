import { existsSync, globSync, readFileSync, writeFileSync } from "node:fs";
import { format, resolveConfig } from "prettier";
import { DEFAULT_LOCALE, PUBLIC_LOCALES, SUPPORTED_LOCALES } from "../../src/locales/locale";
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

// The source catalog comes first; pseudo-locales are compiled from it and have no catalog.
const catalogLocales = [DEFAULT_LOCALE, ...PUBLIC_LOCALES.filter((l) => l !== DEFAULT_LOCALE)];
const pseudoLocales = SUPPORTED_LOCALES.filter((locale) => !PUBLIC_LOCALES.includes(locale));
const catalogs = Object.fromEntries(
  catalogLocales.map((locale) => [locale, readFileSync(catalogPath(locale), "utf8")]),
);
const [english, ...translations] = catalogLocales.map((locale) =>
  catalogContract(catalogs[locale] ?? ""),
);
if (!english) throw new Error("No locales are configured.");
for (const translation of translations) compareCatalogs(english, translation);

// A translation records the English fingerprint it was reviewed against, as in squid-i18n.
const fingerprints = sourceFingerprints(catalogs[DEFAULT_LOCALE] ?? "");
for (const locale of catalogLocales.slice(1)) {
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

// Catalog imports use the namespace m, so each call names its message statically.
// The bundler shares modules between hydrated islands. Emit story-only functions in
// their own modules so a page does not download the entire workbench catalog.
const usedByStory = new Map<string, Set<string>>();
const owners = new Map<string, string>();
for (const file of globSync("src/**/*.{ts,svelte,astro}")) {
  if (file.endsWith(".gen.ts")) continue;
  const story = /\/workbench\/stories\/(\w+)Stories\.svelte$/.exec(file)?.[1]?.toLowerCase() ?? "";
  for (const match of readFileSync(file, "utf8").matchAll(/\bm\.([A-Za-z0-9]+)/g)) {
    const name = match[1];
    if (name === undefined) continue;
    if (story !== "") {
      const uses = usedByStory.get(story) ?? new Set<string>();
      uses.add(name);
      usedByStory.set(story, uses);
    }
    const owner = owners.get(name);
    owners.set(name, owner === undefined || owner === story ? story : "");
  }
}
const unused = Object.keys(english).filter((id) => !owners.has(functionName(id)));
if (unused.length > 0)
  throw new Error(`Delete these messages or use them in src/:\n${unused.join("\n")}`);

function messageModule(name: string): string {
  const owner = owners.get(name);
  return owner !== undefined && owner !== "" ? `messages-${owner}.gen` : "messages.gen";
}
await emit(
  target,
  compileCatalogs(catalogs, pseudoLocales, (name) => owners.get(name) === "", messageModule),
);
for (const [story, uses] of usedByStory) {
  const shared = [...uses].filter((name) => owners.get(name) === "").toSorted();
  const source = compileCatalogs(
    catalogs,
    pseudoLocales,
    (name) => owners.get(name) === story,
    messageModule,
  );
  await emit(
    `src/locales/messages-${story}.gen.ts`,
    [
      source,
      ...(shared.length > 0 ? [`export { ${shared.join(", ")} } from "./messages.gen";`] : []),
    ].join("\n"),
  );
}
const vocabulary = JSON.parse(readFileSync("../contracts/vocabulary.json", "utf8")) as Vocabulary;
await emit(
  "src/locales/vocabulary.gen.ts",
  compileVocabulary(vocabulary, catalogLocales, pseudoLocales),
);
console.info(
  `Compiled ${Object.keys(english).length} messages in ${SUPPORTED_LOCALES.length} locales.`,
);
