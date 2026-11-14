import { format } from "prettier";
import { readFileSync, writeFileSync } from "node:fs";
import { catalogContract, compareCatalogs, generateKeys } from "./catalogs";

const english = catalogContract(readFileSync("src/locales/en.ftl", "utf8"));
compareCatalogs(english, catalogContract(readFileSync("src/locales/zh-CN.ftl", "utf8")));
const generated = await format(generateKeys(english), { parser: "typescript" });
const target = "src/locales/keys.ts";
if (process.argv.includes("--write")) writeFileSync(target, generated);
else if (readFileSync(target, "utf8") !== generated)
  throw new Error("Run bun run i18n:generate and commit the updated keys.");
console.info(`Validated ${Object.keys(english).length} Fluent messages in both locales.`);
