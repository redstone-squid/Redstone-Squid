# Website

The public catalogue. Astro renders every page on the server; Svelte islands handle
interaction. Astro is for pages and layouts; `src/ui` and feature components are Svelte,
which Astro renders statically unless a page hydrates them with a `client:` directive. The
site reads the Python API through an SDK generated from `../contracts/openapi.json`. Use bun
for every command.

## Layout

| Directory        | Owns                                                                     | May import                                                                  |
| ---------------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| `src/pages`      | Routes: read the request, load data, set the status, choose what to show | anything                                                                    |
| `src/layouts`    | Document shells (`CatalogueLayout`, `DocumentHead`, `PlaceholderPage`)   | `navigation`, `ui`, `locales`, `generated`                                  |
| `src/features/x` | Components and browser logic for one area, such as `builds` or `records` | its own directory, `navigation`, `api-client`, `ui`, `locales`, `generated` |
| `src/workbench`  | The `/design-system` playground, its stories, and fixtures               | everything except `server` and `pages`                                      |
| `src/server`     | Runtime config and the page API client. Server only                      | `navigation`, `api-client`, `locales`, `generated`                          |
| `src/api-client` | API errors for both sides, and the islands' write client                 | `locales`, `generated`                                                      |
| `src/navigation` | Localized links, query updates, and native GET form destinations         | `locales`                                                                   |
| `src/locales`    | Locales, URL prefixes, catalogs, and the compiled message functions      | `locales`, `generated`                                                      |
| `src/ui`         | The design system. Knows nothing about the application                   | `ui` only                                                                   |
| `src/generated`  | The OpenAPI SDK. Run `bun run sdk:generate` after the contract changes   |                                                                             |

Import across directories with `@/` (`@/server/api`); only same-directory imports stay
relative. Generated files end in `.gen.ts`, are rebuilt by a script, and are never edited by
hand. `scripts/lint/import-boundaries.ts` enforces this table through ESLint. If a rule blocks you,
move the code to the right layer rather than working around the rule.

## Requests

Middleware (`src/middleware.ts`) runs first and sets seven request locals:

- `Astro.locals.locale`: `en` or `zh-CN`. Every route exists once, without a locale prefix.
  Middleware rewrites `/zh-cn/records` to `/records`, so never add a `zh-cn` directory or read
  the locale from the URL. Messages, labels, and `localizePath(path)` default to the current
  page's locale (the request on the server, `<html lang>` in the browser), so code passes a
  locale only to render another one, such as language links or tests. `Astro.url` is the
  unprefixed route, so a link built from it must go through `localizePath`.
- `Astro.locals.requestedPath`: the prefixed path and query the visitor asked for, kept through
  rewrites to error pages. Links back to the current page, such as language links, use it.
- `Astro.locals.theme` and `Astro.locals.contrast`: what the visitor pinned in the footer,
  which posts to `/appearance`; undefined follows the device. Theme is `light` or `dark`;
  contrast is `more` or `standard`, so a visitor can turn off a device's high contrast. The
  workbench also takes `?theme=`, which wins so screenshots are repeatable.
- `Astro.locals.config`: validated runtime URLs.
- `Astro.locals.api`: a generated SDK client that sends the request locale.
- `Astro.locals.session`: the `/v1/session` snapshot, loaded only when the visitor sends a
  session cookie; such responses are `Cache-Control: private, no-store`. Calls made for the
  visitor, such as sign-out, go through `src/server/session.ts`, which forwards their cookies.

Pages load data in frontmatter and pass plain props down. Feature components never fetch
or read `Astro.locals`. `src/pages/records/` is the reference: copy it for a new screen.

```astro
---
import RecordDetail from "@/features/catalogue/RecordDetail.svelte";
import { recordsGet } from "@/generated/sdk.gen";
import CatalogueLayout from "@/layouts/CatalogueLayout.astro";
import { load } from "@/server/api";

const loaded = await load(
  recordsGet({ client: Astro.locals.api, path: { record_id: Astro.params.id ?? "" } }),
);
if ("status" in loaded) return Astro.rewrite(`/${loaded.status}`);
const record = loaded.data;
---

<CatalogueLayout title={record.title} canonicalPath={`/records/${record.record_id}`}>
  <RecordDetail {record} />
</CatalogueLayout>
```

`load()` turns an API 404 into the 404 page and logs any other failure, including a timeout,
as the 503 page. Responses stream, so load and choose the status before the template renders;
an error thrown inside a component after rendering starts truncates a 200 response. Listings
read their page with `readPageAddress` from `src/server/pagination.ts`, turn the API's `next`
and `prev` anchors into links with `pageHref`, and pass those to the feature, which renders
`Pagination`; mark pages after the first `noindex`.

For query-driven navigation, declare a `Route<State>` alongside the feature that owns its
fields and bind it to the current URL with `route.at(url)`. Use `link` for a fresh destination, `update` for same-page changes (null removes a
key), and `form` for the action, repeated hidden entries, and reset link. Destinations sharing
a scope symbol inherit its resolved values. The workbench declares its appearance scope in
`src/workbench/appearance.ts`; Patterns passes plain form props into its hydrated island.
Pagination replaces its cursor fields together in `src/server/pagination.ts`.

## Adding a page

1. Add `src/pages/<route>.astro`. Load data there and wrap the content in `CatalogueLayout`.
2. Put the components under `src/features/<area>/`. Compose them from `src/ui` first;
   see `src/ui/README.md`. A new `src/ui` component needs two real users and a story;
   anything that knows about builds, records, or the API stays in `features/`.
3. Add its text to the catalogs; see [Text](#text).
4. Cover the route's public behavior in Playwright. Exercise both locales when localization
   affects the URL, rendered content, or workflow. Use Vitest for independent logic and
   edge cases that are easier to exercise without the running site.

## Styling

Components style themselves with Tailwind utilities; pages only compose components, and lint
rejects `class` attributes in `src/pages`. Colors exist only as theme tokens: `light-dark()`
pairs of `oklch()` values in `src/ui/themes.css`, mapped to utilities such as `bg-surface` and
`text-fg-muted` in `src/ui/app.css`. High contrast swaps text, border, and focus tokens for
their `-more` values, so components get it without changes. Forced colors (Windows
contrast themes) erase backgrounds and draw every border, so a state shown only by a
background or border color, such as the current tab, also adds `forced-highlight`. Lint rejects unknown
classes and arbitrary colors. Classes are joined with `cn`, never merged at runtime, so give each
cva variant sole ownership of the properties it sets. A style shared by a family of components
lives in that folder's `styles.ts`, which only `src/ui` imports. Scoped `<style>` blocks are for
selector-heavy styles inside `src/ui`; features and layouts stay utility-only. See
`src/ui/README.md`.

Pages cross-fade on navigation (`@view-transition` in `app.css`), and the header holds still
through its `view-transition-name`. When a card links to a page that shows the same element
large, such as a build's image, give both the same name so it morphs into place. A name must
be unique on the page, so a listing names each card after its item.

## Content security policy

`security.csp` in `astro.config.ts` allows only the scripts and `<style>` elements Astro
bundles, plus images from any HTTPS host. Inline `style` attributes are allowed. Inline event
attributes are not, and Svelte renders `onload` and `onerror` on an element as one, so use an
attachment instead. The policy is skipped under `bun run dev`. Browser tests fail on any
violation, because each one logs a console error; in production, browsers report violations to
`/telemetry/csp`, which logs them. The speculation rules that prefetch pages on hover live in
`src/layouts/speculation-rules.ts`, which the config hashes into the policy.

## Without JavaScript

Public pages must work without JavaScript; scripts only enhance them. Build components from
native elements (`<a>`, `<form>`, `<details>`) so their server-rendered output stays usable.
Never put `<noscript>` in `src/ui` or feature components, which cannot know whether they will
hydrate. A page that hydrates an island with no fallback may add one `<noscript>` notice
beside it, as the workbench pages do.

## Routes that move

Never delete a public URL. When a page moves, add `old → new` to `src/server/redirects.ts`;
middleware redirects it in every locale, and tests check the table against `src/pages`.

The site installs as an app: `src/sw.ts` shows `/offline` when a navigation fails and never
caches pages. Regenerate icons from `public/favicon.svg` with `bun run pwa:icons`.

## Performance

Public pages use device fonts, including CJK fallbacks. The workbench loads its specimen
fonts for repeatable previews. Keep those font imports in the workbench.

Run `bun run check:bundle` and `bun run test:lighthouse` after a production build for changes
that affect page weight or rendering. Lighthouse reports the median of three mobile runs
for the API-backed tag page and the Svelte draft trial. It checks LCP and CLS on both,
transfer size on the tag page, and blocking time on the draft. CI uploads the raw runs
and `summary.json` in its catalogue quality artifact.

## Libraries

Use these before writing your own. Ask before adding a library that overlaps one of them.

| Need                                                     | Use                                                                    |
| -------------------------------------------------------- | ---------------------------------------------------------------------- |
| Accessible interactive primitives (dialogs, menus)       | `bits-ui`, wrapped in `src/ui`                                         |
| Validating untrusted data: API errors, URLs, JSON input  | `zod`                                                                  |
| Utilities such as debounce, groupBy, pick, or retry      | `es-toolkit`                                                           |
| Svelte state helpers: debounced values, watch, listeners | `runed`                                                                |
| Icons                                                    | `@lucide/svelte`                                                       |
| Class variants                                           | `class-variance-authority`, inside the component that owns them        |
| Mocking the API in unit tests                            | `msw`, as in `tests/unit/server-api.test.ts`                           |
| Fetching from the browser inside an island               | `@tanstack/svelte-query`; pages load data on the server                |
| Tables with sorting, column visibility, or grouped rows  | `@tanstack/svelte-table`, wrapped in `src/ui`                          |
| Charts                                                   | `layerchart` (pinned exactly), wrapped in `src/ui`                     |
| Date arithmetic: durations, time zones, "3 days ago"     | `temporal-polyfill` (Node 24 lacks Temporal); format dates in messages |
| Syntax highlighting                                      | `shiki`, through `content/CodeBlock` and its `lang` list               |

`knip.jsonc` lists libraries installed ahead of their first use; remove an entry once code
imports it.

Cookies go through `src/server/cookies.ts`, which owns every cookie's name and flags; lint
rejects `Astro.cookies` and `document.cookie` elsewhere. The API sets its own session cookie.

## Text

Every visible string lives in the Fluent catalogs `src/locales/<locale>/messages.ftl`, with the
same IDs in every locale. `bun run i18n:generate` compiles them into `src/locales/messages.gen.ts`,
one typed function per message. Workbench stories import their own generated module
(e.g. `messages-forms.gen`) so each island downloads only its examples and shared labels:

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

## Code documentation

Follow [TSDoc and comments](code-documentation-style.md). Document contracts at the
declaration and explain surprising decisions beside the code that depends on them.

## Tests

- `tests/unit`: Vitest in jsdom with shared Svelte cleanup. See
  [autocomplete](tests/unit/autocomplete.test.ts) for keyboard behavior and
  [API errors](tests/unit/server-api.test.ts) for HTTP contracts with MSW.
- `tests/e2e`: Playwright against the built site and the fixture API in `tests/fixtures/api.ts`.
  Follow [tag pages](tests/e2e/tag-slugs.spec.ts) for visible page content and HTTP contracts.
  Remove a route from the placeholder matrix when its page ships.
- `tests/review`: axe, overflow, alignment, and recorded workflows of the design-system workbench. CI
  also compares its screenshots with master's and reports differences without failing.
- Use `@tests/` for imports of shared test support. Browser specs import `test` and
  `expect` from `@tests/support/test`. A test fails on any console error, and
  `await expect(page).toPassAxe()` runs axe; `toHaveAlignedControls()` fails when controls
  side by side start at different heights. Server ports and environment live in
  `tests/support/servers.ts`. Local runs reuse servers already on those ports, which may be
  another worktree's stale build; set `TEST_APP_PORT` and `TEST_API_PORT` to run apart.

- Name the regression each test catches. Prefer one case at the cheapest layer that can
  observe it. Add a second layer when it covers a distinct risk such as hydration, browser
  focus, or the HTTP response. Check for an existing case before adding one.
- Query by role and accessible name. Use generated catalogue messages with an explicit
  locale in Playwright. Assert errors on their fields and feedback in the status region.
  Keep literal expectations for fixture data, public URLs, and translation output.
- Assert the effect of the action. A deadline should abort a pending request. A keyboard
  interaction should move focus or select a value. Use fake timers for debounce and wait
  for observable changes in browser tests.
- Remove assertions that only pin incidental markup, class lists, or internal ordering.
  Keep those details where they are the contract under test. When a helper's only callers
  are its tests, check whether the code and its tests can both go.
- Keep actions and outcomes visible in the spec. Use fixtures for shared setup and
  extract a helper when repeated setup obscures the test. Comment on timing or browser
  constraints a reader would otherwise have to reconstruct.

## Checks

Pre-commit runs Prettier on staged web files. Review and restage any formatting
changes before retrying the commit. Pre-push runs `bun run lint` when the push
includes web changes. Install the web dependencies with `bun install --frozen-lockfile`
from `web/` before committing web work.

`bun run lint` runs ESLint, stylelint, and knip. Beyond the TypeScript and Svelte presets:

- `scripts/lint/` holds the custom rules: `squid/import-boundaries` enforces the layer table,
  `squid/no-raw-text` keeps text in the catalogs, and `squid/no-raw-elements` makes features
  and layouts use `src/ui` controls and no `<style>` blocks. `arbitrary-values.ts` limits
  arbitrary Tailwind values to `src/ui`; features and pages also get no gap, margin or padding
  utilities, so they space parts with `Layout`, `Section` and component padding. Disable a
  rule on one line only with a reason after `--`.
- `strict-boolean-expressions` requires explicit checks such as `items.length > 0` or
  `value !== ""`, so `0` and `""` are never mistaken for missing values.
- `eslint.config.js` enables chosen unicorn and SonarJS rules rather than their presets,
  plus `eslint-plugin-regexp`. Add a rule there when it catches something real.
- stylelint requires colors, z-indexes, and durations to come from tokens, in CSS files and
  `<style>` blocks alike; only `src/ui/themes.css` writes `oklch()`.
- knip fails on unused files, exports, and dependencies. Delete dead code instead of
  exporting it for later.

From `web/`, format touched files with Prettier and run `bun run lint`, `bun run check`,
and `bun run test` as applicable. Vitest checks coverage for locale, UI, feature, and
workbench code. Run `bun run build` before the relevant Playwright tests, such as
`bunx playwright test tests/e2e --project=chromium-desktop`. Check visual changes on the
workbench page in `bun run dev`; run `bun run test:review -- --grep <group>` when a change
could break accessibility or layout. CI runs all of these.
