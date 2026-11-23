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
- `variants.ts` is gone. A style lives in the component that owns it; a style shared by a
  folder lives in that folder's `styles.ts`, imported only inside `src/ui`. Code that wants a
  button's look renders `Button` (bits-ui triggers through their `child` snippet).
- Builds and records share `features/catalogue`, since a record shows its holders as build
  cards and features cannot import each other.

## Progress

- Phase 0 and Phase 1 are done: tokens, the Svelte-only `src/ui` grouped by folder, the
  catalogue features in Svelte, and the workbench with one stories page per folder and a
  coverage test.

## Build order

Each phase ends with workbench stories, unit tests (keyboard and focus), review screenshots and axe for every new component, and the README table updated.

**Phase 0: foundations (no new components)**
1. Add the tokens in §2.4: color roles in both themes with contrast checks, type scale, weights, tracking, spacing and sizes, radii, motion, the z utilities, and `themeColor` in `themes.ts`.
2. Fix the base layer: neutral `h1`–`h3` sizes and CJK tracking.
3. Fix the existing violations in §0 (items 1–8) inside `src/ui` and layouts.
4. Add `@tanstack/svelte-table` and `layerchart` (exact version pin). Remove `@tanstack/svelte-query` from the knip ignore list once it is first used.

**Phase 1: static primitives.** No interactivity, renderable from Astro without hydration.
- `Layout`, `Container`, `Heading`, `Text`, `Link`, `Icon`, `VisuallyHidden`, `SkipLink`, `Button` (with `href`), `Badge`, `Chip` (link and static), `Card`, `Notice`, `Timestamp`, `DescriptionList`, `StatGroup`, `Image`, `Avatar`, `Code`, `Prose`, `Disclosure`, `Skeleton`, `Progress`, `EmptyState`, `ErrorState`, `Breadcrumbs`, `Pagination`, `NavList`, `Tabs` (link mode), `SegmentedControl` (link mode), `Timeline`, `Diff`, `List`/`ListItem`.
- Then migrate the existing features (`BuildCard`, `RecordCard`, `RecordDetail`, the records list, the tag page, `PlaceholderPage`, `CatalogueLayout`) and the workbench to these components.
- Delete `src/ui/astro/`, `BuildPreview.astro`, `SummaryCard.astro`, `features/catalogue/Pagination.astro`, and the duplicate workbench messages.
- *Depends on:* Phase 0.

**Phase 2: form primitives.**
- `FieldFrame` fix, `TextField`, `TextareaField`, `SelectField`, `CheckField`, `Switch`, `ChoiceGroup`, `FieldGroup`, `RangeField`, `UnitField`, `CodeEntry`, `ErrorSummary`, `FormActions`, `SaveStatus`, `FileDrop`.
- Then `Autocomplete` (refit), `Combobox` (bits-ui), `ListField`.
- *Depends on:* Phase 1 (`Text`, `Icon`, `Chip`).

**Phase 3: overlays and interactive navigation.**
- `Dialog` (rewritten), `Sheet`, `Popover`, `Tooltip`, `Menu` (with the `details` fallback), `Tabs` panel mode, `SegmentedControl` radio mode, `CopyButton`, `Spinner`, `Announcer`.
- *Depends on:* Phase 2 for the dialog contents.

**Phase 4: data and charts.**
- `DataTable`: `@tanstack/svelte-table`, server-driven sort, visibility, expansion, pinned column and the no-JS link model. It needs Phase 3 `Menu` for columns.
- `Gallery`: needs Phase 3 `Dialog`.
- `Chart` frame, `SeriesChart`, `RangeChart`, `DistributionBar` on LayerChart: static server rendering first, then interactive tooltips and keyboard.

**Phase 5: patterns.**
- `PageHeader`, `Section`, `SplitLayout`, `NavBar`, `SearchBox`, `FilterPanel`, `ActiveFilters`, `ResultsToolbar`, `ResultsView`, `ConfirmDialog`, `SettingsList`, `MediaUploader`, `OneTimeSecret`, `StepFlow`, `FlashNotice`.
- Then rebuild `CatalogueLayout` and the header with `NavBar` and the `UserMenu` island once `/v1/session` exists.

**Phase 6: application infrastructure.** Parallel with Phase 5.
- `src/ui/lib/url-state.ts`, `focus.ts`, `query-client.ts` (one QueryClient per page).
- `src/features/api-client/mutation.ts`: CSRF, Idempotency-Key, If-Match, ProblemDetail parsing with zod, the error-code map, and the same-origin proxy decision (API item 52).
- `features/submissions/draft-sync.ts`: the operation queue, with Vitest coverage for rebase, conflict, offline and replay.

**Phase 7: enforcement.**
- ESLint bans:
  - arbitrary Tailwind values outside `src/ui`, and inside it except in an allowlist;
  - importing any `src/ui/**/styles.ts` outside `src/ui`;
  - `layerchart`, `bits-ui` and `@tanstack/svelte-table` imports outside `src/ui`;
  - raw `<button>`, `<input>`, `<select>`, `<textarea>`, `<table>`, `<dialog>` and `<img>` elements in `src/features` and `src/layouts`, which must use the `ui` components (escape hatch: a lint-disable with a reason).
- Stylelint: the z-index ladder only, no raw durations.
- A story test asserting every `src/ui` component has a workbench story and an axe-checked review screenshot in both themes, at desktop and mobile.
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
