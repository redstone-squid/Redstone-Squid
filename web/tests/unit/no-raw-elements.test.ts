import * as astroParser from "astro-eslint-parser";
import { RuleTester } from "eslint";
import svelteParser from "svelte-eslint-parser";
import { describe, it } from "vitest";
import { noRawElements } from "../../scripts/lint/no-raw-elements";

RuleTester.describe = describe;
RuleTester.it = it;
const error = { messageId: "element" };

new RuleTester({ languageOptions: { parser: svelteParser } }).run(
  "no-raw-elements (Svelte)",
  noRawElements,
  {
    valid: [
      { filename: "A.svelte", code: "<Button>{label}</Button>" },
      { filename: "A.svelte", code: '<input type="hidden" name="return" value={path} />' },
      { filename: "A.svelte", code: "<form><a href={href}>{label}</a></form>" },
    ],
    invalid: [
      { filename: "A.svelte", code: '<button type="button">{label}</button>', errors: [error] },
      { filename: "A.svelte", code: "<input type={kind} />", errors: [error] },
      { filename: "A.svelte", code: "<table><tr><td>{n}</td></tr></table>", errors: [error] },
      { filename: "A.svelte", code: '<img src={url} alt="" />', errors: [error] },
    ],
  },
);

new RuleTester({ languageOptions: { parser: astroParser } }).run(
  "no-raw-elements (Astro)",
  noRawElements,
  {
    valid: [
      { filename: "A.astro", code: '<input type="hidden" name="return" value={path} />' },
      { filename: "A.astro", code: "<Image src={url} alt={alt} width={32} height={32} />" },
    ],
    invalid: [
      { filename: "A.astro", code: "<select name={name}></select>", errors: [error] },
      { filename: "A.astro", code: "<dialog open></dialog>", errors: [error] },
      { filename: "A.astro", code: "<textarea name={name} />", errors: [error] },
    ],
  },
);
