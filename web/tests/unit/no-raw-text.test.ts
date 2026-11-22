import * as astroParser from "astro-eslint-parser";
import { RuleTester } from "eslint";
import svelteParser from "svelte-eslint-parser";
import { describe, it } from "vitest";
import { noRawText } from "../../scripts/lint/no-raw-text";

RuleTester.describe = describe;
RuleTester.it = it;
const error = { messageId: "text" };

new RuleTester({ languageOptions: { parser: svelteParser } }).run(
  "no-raw-text (Svelte)",
  noRawText,
  {
    valid: [
      { filename: "A.svelte", code: "<p>{t('title')}</p>" },
      { filename: "A.svelte", code: "<span>→ · 42 ✓</span>" },
      { filename: "A.svelte", code: '<span aria-hidden="true"><b>R</b></span>' },
      { filename: "A.svelte", code: "<input aria-label={t('search')} placeholder={hint} />" },
      { filename: "A.svelte", code: '<a href="/records" class="link">{label}</a>' },
    ],
    invalid: [
      { filename: "A.svelte", code: "<p>Loading…</p>", errors: [error] },
      { filename: "A.svelte", code: "<p>{count} 项</p>", errors: [error] },
      { filename: "A.svelte", code: '<button aria-label="Close">×</button>', errors: [error] },
      { filename: "A.svelte", code: '<img alt="Door {n}" src={url} />', errors: [error] },
    ],
  },
);

new RuleTester({ languageOptions: { parser: astroParser } }).run("no-raw-text (Astro)", noRawText, {
  valid: [
    { filename: "A.astro", code: "<h1>{title}</h1>" },
    { filename: "A.astro", code: '<span class="mark" aria-hidden="true">R</span>' },
    { filename: "A.astro", code: '<a href="/" title={t("home")}>↗</a>' },
  ],
  invalid: [
    { filename: "A.astro", code: "<h1>Records</h1>", errors: [error] },
    { filename: "A.astro", code: '<img src="/a.png" alt="Copper door" />', errors: [error] },
  ],
});
