# Website

The public catalogue. Astro renders every page on the server; Svelte islands handle
interaction. The site reads the Python API through an SDK generated from
`../contracts/openapi.json`. Use bun for every command.

## Layout

| Directory        | Owns                                                                     | May import                                             |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------------------------ |
| `src/pages`      | Routes: read the request, load data, set the status, choose what to show | anything                                               |
| `src/layouts`    | Document shells (`CatalogueLayout`, `DocumentHead`, `PlaceholderPage`)   | `lib`, `ui`, `locales`, `generated`                    |
| `src/features/x` | Components and browser logic for one area, such as `builds` or `records` | its own directory, `lib`, `ui`, `locales`, `generated` |
| `src/workbench`  | The `/design-system` playground, its stories, and fixtures               | everything except `server` and `pages`                 |
| `src/server`     | Runtime config and the API client. Server only                           | `lib`, `locales`, `generated`                          |
| `src/lib`        | Code that runs on both sides: i18n, pagination, problem details          | `locales`, `generated`                                 |
| `src/ui`         | The design system. Knows nothing about the application                   | `ui` only                                              |
| `src/generated`  | The OpenAPI SDK. Run `bun run sdk:generate` after the contract changes   |                                                        |

`scripts/import-boundaries.ts` enforces this table through ESLint. If a rule blocks you,
move the code to the right layer rather than working around the rule.

## Requests

Middleware (`src/middleware.ts`) runs first and sets three request locals:

- `Astro.locals.locale`: `en` or `zh-CN`. Every route exists once, without a locale prefix.
  Middleware rewrites `/zh-cn/records` to `/records`, so never add a `zh-cn` directory
  or read the locale from the URL. Build links with `localizePath(path, locale)`.
- `Astro.locals.config`: validated runtime URLs.
- `Astro.locals.api`: a generated SDK client that sends the request locale.

Pages load data in frontmatter and pass plain props down. Feature components never fetch
or read `Astro.locals`:

```astro
---
import CatalogueLayout from "../../layouts/CatalogueLayout.astro";
import { recordsGet } from "../../generated/sdk.gen";
import { unwrap } from "../../server/api";

const result = await recordsGet({
  client: Astro.locals.api,
  path: { record_id: Number(Astro.params.id) },
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
3. Add messages to both `src/locales/en.ftl` and `src/locales/zh-CN.ftl`, then run
   `bun run i18n:generate`. Use `translate(locale, key)`. Don't hard-code text.
4. Cover pure logic with a Vitest test in `tests/unit` and the route with a Playwright
   test in `tests/e2e`. Check both locales.

## Checks

From `web/`: `bun run format`, `bun run lint`, `bun run check`, `bun run test`, then
`bun run build` and the relevant Playwright tests, such as
`bunx playwright test tests/e2e --project=chromium-desktop`. Run `bun run test:review`
after visual changes. CI runs all of these.
