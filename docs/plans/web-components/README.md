# Web component system

The plan for a component system that covers every screen the website needs to expose
squid/core, so screens are composed from `web/src/ui` instead of one-off markup.

- [screens.md](screens.md): every screen, the user's goal, and what it must show.
- [components.md](components.md): the `src/ui` primitives and patterns, feature components,
  tokens, and the interaction patterns every screen follows.
- [api-gaps.md](api-gaps.md): API changes the screens need. The UI is designed for users,
  not around the current API; land these before building the screen that needs them.

## Decisions

- Every `src/ui` component is written once in Svelte 5. Astro renders it statically (no
  `client:` directive ships no JavaScript) or as an island. Only layouts that read
  `Astro.locals` stay Astro.
- Three layers: domain-free primitives and patterns in `src/ui`, Squid-aware components in
  `src/features/<area>`.
- bits-ui for interactive primitives, lucide for icons, TanStack Query for island data and
  mutations, `@tanstack/svelte-table` v9 for `DataTable`, LayerChart 2.x (pinned exactly) for
  charts. bits-ui, TanStack Table and LayerChart are imported only inside `src/ui`.
- No toasts. Feedback is an inline `Notice` or `SaveStatus`.
- Search with `scope=all` shows cards. Records default to the table; builds default to cards
  with a table toggle. The view lives in the URL.
- Lint enforces the system: no arbitrary Tailwind values or raw style constants outside
  `src/ui`, and no raw interactive elements in features and layouts.
- Astro is for pages and layouts; ui and feature components are Svelte, so Astro slot
  wrappers only appear at page and layout boundaries.
- A style lives in the component that owns it; a class string shared by a folder lives in
  that folder's `styles.ts`, imported only inside `src/ui`. Code that wants a button's look
  renders `Button` (bits-ui triggers through their `child` snippet).
- Tailwind stays for layout, spacing and color. Scoped `<style>` blocks are allowed inside
  `src/ui` only, for selector-heavy styles; features and layouts stay utility-only.
- Builds and records share `features/catalogue`, since a record shows its holders as build
  cards and features cannot import each other.
- API errors and the islands' write client live in `src/api-client`, a layer that server,
  features and the workbench import, rather than `features/api-client`, which no other
  feature could have used.

### Same-origin API (API item 52)

Islands call the API on the site's own origin; there is no Astro proxy. The gateway already
routes `/v1` on `SITE_URL` to the API and `API_BASE_URL` defaults to `SITE_URL`
(`docs/docker.md`), so the session cookie is first-party, and the CSP's `default-src 'self'`
already permits the calls. A proxy would add a hop, a second place to forward cookies, locale
and request ids, and a second failure mode for every write, for no security gain. `BrowserApi`
takes the base URL from the page (`config.apiBaseUrl`), so development, with the API on
another port of the same host, works through the API's CORS settings. Only `src/server` may
read cookies, so islands fetch the CSRF token once per page from `GET /v1/auth/csrf`. If the
API ever moves to another site, the CSP needs a `connect-src` and its cookies become
third-party; revisit this then.

## Status and resuming

Phases 0 to 7 are done; Phase 8 (screens) is next. Every §2 component exists; the
"Need → Use" table in `web/src/ui/README.md` lists them. `src/ui/lib`, `src/api-client` and
`features/submissions/draft-sync.ts` wait for their screens.

To resume, read `screens.md` for the next screen, its API items in `api-gaps.md`, and
`components.md` §3 for its feature components, then compose it from `src/ui`.

Open items:

- The `UserMenu` island, built with the header screen. Nothing blocks it: `/v1/session`
  exists and islands call the API directly (API item 52). Its place is marked in
  `CatalogueLayout`'s actions.
- `FlashNotice` takes text; the one-shot flash cookie in `src/server/cookies.ts` waits for
  the first POST-redirect-GET endpoint.
- `WorkbenchLayout` keeps its own sidebar (§0 item 22); `SplitLayout` would make its `aside`
  and `NavList` two landmarks with one name.
- Audit each screen as it ships, not the workbench: keyboard walkthrough of the whole page
  (header, filters, results, footer), focus after navigation and after server-driven filter
  changes, Tab through collapsed `NavBar` and `SplitLayout` popovers, Lighthouse, and the
  page's JavaScript weight (a LayerChart island has never been measured on a real page).

Departures from §2.3: `NavBar` and `SplitLayout` collapse into native popovers rather than
a `Sheet` or `<details>`. One copy of the markup serves both layouts through a container
query (`popover-inline` in `app.css`), so filter fields keep their ids and values and nothing
needs hydration; the price is light dismiss without a focus trap. `NavBar` takes `home` as
data, because Astro passes a Svelte component only one slotted child. `Announcer` is not an
island: `announce()` writes to the server-rendered region, which saved 23 KiB of runtime on
every page. `SettingsList` rows take a labelled control, since `Switch` labels itself.
`ResultsToolbar` has no `columns`; `DataTable` renders its column menu. `FilterPanel`
takes a preformatted `activeLabel`.

Departures from §2.2: `RangeField` takes `separator` and `inputmode` instead of `bounds` and
`step`, which a text input cannot enforce; `UnitField` submits the typed number and unit id
(`name`, `unitName`); `FileDrop` passes skipped files as a second `onFiles` argument.
`DistributionBar` is HTML bars, not LayerChart, so labels wrap and stay text. The
`DataTable` header is not sticky to the page, because its horizontal scroll container
captures `position: sticky`; revisit with a page-height scroll region if long tables need it.

Things the code does not make obvious:

- A new `src/ui` folder needs an entry in `src/workbench/groups.ts`, a line in
  `src/pages/design-system/[group].astro`, and one in the `it.each` list of
  `tests/unit/workbench-stories.test.ts`. Vitest coverage for `src/ui` stays at 90% lines,
  functions and statements and 85% branches.
- Broken-image demos use `https://media.fixture.invalid/broken.png`; a real 404 fails the
  console check.
- ESLint resolves types imported from a `.svelte` module as `any`, so shared prop types
  belong in a `.ts` file beside the component.
- Data-driven geometry (`style:width`) is allowed with an `eslint-disable
svelte/no-inline-styles` block and a reason.
- `src/ui/image-fallback.ts` must stay a non-module script loaded only by `DocumentHead`;
  as a module, or loaded elsewhere, it splits into a chunk that fails on the offline page.

## Build order

Each phase ends with workbench stories, unit tests (keyboard and focus), axe on every new component, and the README table updated.

**Phase 0: foundations.** Done.

**Phase 1: static primitives and migration.** Done. `PageHeader`, `Section` and `Spinner`
were built here, ahead of their phases.

**Phase 2: form primitives.** Done.

- `FieldFrame` fix, `TextField`, `TextareaField`, `SelectField`, `CheckField`, `Switch`, `ChoiceGroup`, `FieldGroup`, `RangeField`, `UnitField`, `CodeEntry`, `ErrorSummary`, `FormActions`, `SaveStatus`, `FileDrop`.
- Then `Autocomplete` (refit), `Combobox` (bits-ui), `ListField`.
- _Depends on:_ Phase 1 (`Text`, `Icon`, `Chip`).

**Phase 3: overlays and interactive navigation.** Done.

- `Dialog` (rewritten), `Sheet`, `Popover`, `Tooltip`, `Menu` (with the `details` fallback, then replace `LanguageSelector`'s hand-built menu), `Tabs` panel mode, `SegmentedControl` radio mode, `CopyButton`, `Announcer`.
- _Depends on:_ Phase 2 for the dialog contents.

**Phase 4: data and charts.** Done.

- `DataTable`: `@tanstack/svelte-table`, server-driven sort, visibility, expansion, pinned column and the no-JS link model. It needs Phase 3 `Menu` for columns.
- `Gallery`: needs Phase 3 `Dialog`.
- `Chart` frame, `SeriesChart`, `RangeChart`, `DistributionBar` on LayerChart: static server rendering first, then interactive tooltips and keyboard.

**Phase 5: patterns.** Done.

- `SplitLayout`, `NavBar`, `SearchBox`, `FilterPanel`, `ActiveFilters`, `ResultsToolbar`, `ResultsView`, `ConfirmDialog`, `SettingsList`, `MediaUploader`, `OneTimeSecret`, `StepFlow`, `FlashNotice`.
- Then rebuild `CatalogueLayout` and the header with `NavBar`; the `UserMenu` island follows in Phase 8.

**Phase 6: application infrastructure.** Done.

- `src/ui/lib/url-state.ts`, `focus.ts`, `query-client.ts` (one QueryClient per page).
- `src/api-client/mutation.ts`: CSRF, Idempotency-Key, If-Match, ProblemDetail parsing with zod, the error-code map, and the same-origin proxy decision (API item 52).
- `features/submissions/draft-sync.ts`: the operation queue, with Vitest coverage for rebase, conflict, offline and replay.

**Phase 7: enforcement.** Done.

- ESLint bans:
  - arbitrary Tailwind values outside `src/ui`, and inside it except in an allowlist;
  - importing any `src/ui/**/styles.ts` outside `src/ui`;
  - `layerchart`, `bits-ui` and `@tanstack/svelte-table` imports outside `src/ui`;
  - raw `<button>`, `<input>`, `<select>`, `<textarea>`, `<table>`, `<dialog>` and `<img>` elements in `src/features` and `src/layouts`, which must use the `ui` components (escape hatch: a lint-disable with a reason).
- Stylelint: the z-index ladder only, no raw durations.
- A story test asserting every `src/ui` component has a workbench story, checked by axe in both themes, at desktop and mobile.
- Rewrite `src/ui/README.md` as the "Need → Use" table for this spec.
- Add an AGENTS.md rule: a new `src/ui` component needs two real users and a story; anything domain-aware goes in `features/`.

**Phase 8: screens.** In dependency order. Each new screen needs its API items first.

1. P7 and P8, using existing data, then API 11–14.
2. P5, needs API 5–6.
3. P2, P3 and P4, need API 18–20.
4. P12 and P11, need API 21–22.
5. P13, needs API 24–25.
6. A1 and the header, need API 1 and 3.
7. A6 and A7, need API 29–31.
8. S1, S2 and S3, need API 32–36.
9. L1 and L2, no API changes needed.
10. A2–A5.
11. P9 and P10, need API 15–17.
12. M1–M4, need API 8, 10, 37, 39–41.
13. X1–X8.
14. P1, P6, P14–P16.
