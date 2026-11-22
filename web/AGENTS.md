# Website

The public catalogue. Astro renders every page on the server; Svelte islands handle
interaction. The site reads the Python API through an SDK generated from
`../contracts/openapi.json`. Use bun for every command.

## Layout

| Directory        | Owns                                                                     | May import                                      |
| ---------------- | ------------------------------------------------------------------------ | ----------------------------------------------- |
| `src/pages`      | Routes: read the request, load data, set the status, choose what to show | anything                                        |
| `src/layouts`    | Document shells (`CatalogueLayout`, `DocumentHead`, `PlaceholderPage`)   | `ui`, `locales`, `generated`                    |
| `src/features/x` | Components and browser logic for one area, such as `builds` or `records` | its own directory, `ui`, `locales`, `generated` |
| `src/workbench`  | The `/design-system` playground, its stories, and fixtures               | everything except `server` and `pages`          |
| `src/server`     | Runtime config and the API client. Server only                           | `locales`, `generated`                          |
| `src/locales`    | Locales, URL prefixes, catalogs, and the compiled message functions      | `locales`, `generated`                          |
| `src/ui`         | The design system. Knows nothing about the application                   | `ui` only                                       |
| `src/generated`  | The OpenAPI SDK. Run `bun run sdk:generate` after the contract changes   |                                                 |

Import across directories with `@/` (`@/server/api`); only same-directory imports stay
relative. Generated files end in `.gen.ts`, are rebuilt by a script, and are never edited by
hand. `scripts/lint/import-boundaries.ts` enforces this table through ESLint. If a rule blocks you,
move the code to the right layer rather than working around the rule.

## Requests

Middleware (`src/middleware.ts`) runs first and sets three request locals:

- `Astro.locals.locale`: `en` or `zh-CN`. Every route exists once, without a locale prefix.
  Middleware rewrites `/zh-cn/records` to `/records`, so never add a `zh-cn` directory or read
  the locale from the URL. Messages, labels, and `localizePath(path)` default to the current
  page's locale (the request on the server, `<html lang>` in the browser), so code passes a
  locale only to render another one, such as language links or tests.
- `Astro.locals.config`: validated runtime URLs.
- `Astro.locals.api`: a generated SDK client that sends the request locale.

Pages load data in frontmatter and pass plain props down. Feature components never fetch
or read `Astro.locals`:

```astro
---
import CatalogueLayout from "../../layouts/CatalogueLayout.astro";
import { recordsGet } from "../../generated/sdk.gen";
import { unwrap } from "../../server/api";

const id = Astro.params.id;
if (!id) return Astro.rewrite("/404");
const result = await recordsGet({
  client: Astro.locals.api,
  path: { record_id: id },
});
if (result.response?.status === 404) return Astro.rewrite("/404");
const record = unwrap(result); // Other failures throw ApiError and render a 500.
---

<CatalogueLayout title={record.title}>...</CatalogueLayout>
```

Responses stream, so set the status and throw any errors before the template renders.
An error thrown inside a component after rendering starts truncates a 200 response.

## Adding a page

1. Add `src/pages/<route>.astro`. Load data there and wrap the content in `CatalogueLayout`.
2. Put the components under `src/features/<area>/`. Compose them from `src/ui` first;
   see `src/ui/README.md`.
3. Add its text to the catalogs; see [Text](#text).
4. Cover pure logic with a Vitest test in `tests/unit` and the route with a Playwright
   test in `tests/e2e`. Check both locales.

## Libraries

Use these before writing your own. Ask before adding a library that overlaps one of them.

| Need                                                     | Use                                                    |
| -------------------------------------------------------- | ------------------------------------------------------ |
| Accessible interactive primitives (dialogs, menus)       | `bits-ui`, wrapped in `src/ui`                         |
| Validating untrusted data: API errors, URLs, JSON input  | `zod`                                                  |
| Utilities such as debounce, groupBy, pick, or retry      | `es-toolkit`                                           |
| Svelte state helpers: debounced values, watch, listeners | `runed`                                                |
| Icons                                                    | `@lucide/svelte`                                       |
| Class variants                                           | `class-variance-authority`, as in `src/ui/variants.ts` |
| Mocking the API in unit tests                            | `msw`, as in `tests/unit/server-api.test.ts`           |

`knip.jsonc` lists libraries installed ahead of their first use; remove an entry once code
imports it.

Cookies go through `src/server/cookies.ts`, which owns every cookie's name and flags; lint
rejects `Astro.cookies` and `document.cookie` elsewhere. The API sets its own session cookie,
so create that module only when the site itself needs a cookie.

## Text

Every visible string lives in the Fluent catalogs `src/locales/<locale>/messages.ftl`, with the
same IDs in every locale. `bun run i18n:generate` compiles them into `src/locales/messages.gen.ts`,
one typed function per message, so an island ships only the messages it calls:

```ts
import * as m from "@/locales/messages.gen";

m.recordHolderCount({ count: holders.length });
```

- IDs are kebab-case and start with their area (`record-holder-count`).
- Arguments are typed by how the catalogs use them: a plural selector or `NUMBER()` takes a
  number, `DATETIME()` a date. Format numbers and dates inside the message, not in code.
- Domain values such as record criteria, build kinds, and statuses have one label each, in
  `squid/core/vocabulary.py`, shared with the bot. `just export-vocabulary` writes them to
  `contracts/vocabulary.json`, which compiles into `src/locales/vocabulary.gen.ts`:
  `recordCriterionLabel(record.criterion)`.
- To pick a message by any other value, write a map checked against the union type, such as
  `{ required: m.trialRequired, ... } satisfies Record<DraftError, unknown>`. Never build a
  message name from a string.
- The build fails when a message is not called from `src/`, so delete text with its feature.
- Open any page under `/en-xa` to see the pseudo-locale: every message is accented,
  bracketed, and about 40% longer, so hard-coded text and layouts that cannot grow stand out.
- Each translation's `source-revisions.json` records the English it was reviewed against.
  When you change English wording, update every translation and run `bun run i18n:review`;
  the build fails until you do.

## Tests

- `tests/unit`: Vitest in jsdom. Render Svelte components with Testing Library and query
  by role and accessible name. Coverage thresholds apply to `src/locales`, `src/ui`,
  `src/features`, and `src/workbench`.
- `tests/e2e`: Playwright against the built site and the fixture API in `tests/fixtures/api.ts`.
- `tests/review`: screenshots, axe, and recorded workflows of the design-system workbench.
- Browser specs import `test` and `expect` from `tests/support/test`. A test fails on any
  console error, and `await expect(page).toPassAxe()` runs axe. Server ports and environment
  live in `tests/support/servers.ts`.

## Checks

`bun run lint` runs ESLint, stylelint, and knip. Beyond the TypeScript and Svelte presets:

- `squid/import-boundaries` enforces the layer table and `squid/no-raw-text` keeps text in
  the catalogs (both in `scripts/lint/`).
- `strict-boolean-expressions` requires explicit checks such as `items.length > 0` or
  `value !== ""`, so `0` and `""` are never mistaken for missing values.
- stylelint requires colors to come from tokens in `src/ui/tokens.css`.
- knip fails on unused files, exports, and dependencies. Delete dead code instead of
  exporting it for later.

From `web/`: `bun run format`, `bun run lint`, `bun run check`, `bun run test`, then
`bun run build` and the relevant Playwright tests, such as
`bunx playwright test tests/e2e --project=chromium-desktop`. Run `bun run test:review`
after visual changes. CI runs all of these.
