# A Public Catalogue for Redstone-Squid

> **Status.** Not started; approved as the plan of record on 2026-08-10. The findings below are
> verified in-tree, not hypothetical — finding 1 breaks every build detail response the moment the
> contract change lands, and findings 2 and 3 are live defects today regardless of whether this
> ships. Amend this document in place as phases land, calling out where building it proved part of
> it wrong rather than silently rewriting.

## Context

`docs/plans/rest-api.md` shipped a real HTTP surface through Phase 8, but nothing consumes it. The
catalogue — 66 tables, a safe query language, computed record competitions, weighted voting — is
still reachable only by people already in the Discord server. Builders who find us through a search
engine or a linked record have nowhere to land.

This plan builds a bilingual public catalogue at `/web`: Astro SSR, small React islands, custom CSS,
Bun, and a generated OpenAPI SDK. It is read-only and complements Discord. Authentication,
submissions, voting, moderation, and server configuration stay out of scope and stay in Discord.

Astro SSR rather than a static or client-rendered build, because detail pages must serve complete
HTML and metadata before JavaScript runs — a record page that renders empty to a crawler is a record
page nobody finds. [Astro SSR documentation](https://docs.astro.build/en/guides/on-demand-rendering/)

## What already exists and must be reused

- **`squid/api/v1/`** — builds, records, search, tags, versions, schematics. `GET /builds` already
  serves cursor-paged public search; `GET /builds/{id}` already returns media, timings, patterns,
  restrictions, and links. The catalogue needs three contract *extensions*, not new endpoints.
- **`squid/api/errors.py`** — RFC 9457 `application/problem+json`, locale-aware, redacted,
  trace-correlated via `X-Error-ID`. The site maps these to states; it does not invent an error
  vocabulary.
- **`squid/api/i18n.py` and `squid/core/i18n.py`** — `Accept-Language` negotiation against
  `SUPPORTED_LOCALES = frozenset({"en", "zh-CN"})` (`squid/core/i18n.py:13`), already applied to
  every error response.
- **`squid/api/v1/search.py`** — `GET /search/suggest` (line 42) backs the search composer;
  `PUBLIC_SEARCH_STATUSES = frozenset({"confirmed"})` (line 32) is the public visibility fence;
  `hydrate_builds` (lines 112-126) loads authoritative domain `Build` objects via
  `build_queries.get_many`.
- **`tests/unit/api/test_openapi_contract.py`** — the schemathesis 4.x harness every new or changed
  route registers with.

## Lessons from the FYP

The prior project is the reason this plan is scoped the way it is.

Retain: strict TypeScript, generated API contracts, frozen dependencies, CI, preview builds,
responsive checks, redirect sanitization, deliberate loading/error/empty states, and the transform
layer between API data and views. Its real-user trial with roughly 60 students produced more useful
findings than its 475 unit tests did.

Correct:

- The ~55k-line frontend was too broad. A 1,396-line instructor hook and a 1,156-line review
  workspace each concentrated queries, mutations, modal state, and transformations.
- No Playwright suite, no automated accessibility testing, no visual regression, no enforced
  coverage thresholds.
- Installed-but-unused UI primitives grew the audit and dependency surface while the result still
  looked like generic dashboard UI.
- Mock/backend branches were mixed into workflow hooks. Fixtures here live at the HTTP boundary.
- No Sentry, OpenTelemetry, TanStack Query, broad UI kit, or global client state in v1 without a
  demonstrated need.

## Findings — resolved before or during the contract phase

Verified in-tree during design.

1. **Blocking — extending `BuildSummary` breaks every `BuildDetail` response.** `BuildDetail`
   subclasses `BuildSummary` and already declares `versions`, `opening_time`, and `closing_time`
   (`squid/api/v1/schemas/builds.py:195-207`). Its constructor spreads the summary and then passes
   those fields again explicitly:

   ```python
   summary = BuildSummary.from_domain(build)
   return cls(
       **summary.model_dump(),
       version_spec=build.version_spec,
       versions=list(build.versions),
       ...
   ```

   (`squid/api/v1/schemas/builds.py:213-218`.) Moving version and timing fields onto the summary
   makes that `TypeError: got multiple values for keyword argument 'versions'` on every detail
   request. **Phase 1 must delete the now-inherited declarations and their constructor arguments
   from `BuildDetail`, not just add fields to `BuildSummary`.** `RecordDetail(RecordSummary)` has the
   same shape; `holder_builds` is genuinely new there, so it is safe, but the same check applies.

2. **Live defect — the documentation workflow targets a branch that does not exist.** The
   repository's default branch is `master`, but `.github/workflows/docs.yml` gates on `main` in
   three places: the push trigger (line 6), the `upload-pages-artifact` step (line 45), and the
   deploy job (line 52). Documentation has therefore never deployed from a push. `zensical.toml:7`
   carries the same error in `edit_uri = "edit/main/docs/"`, breaking every "edit this page" link.
   All four are fixed in Phase 4. Changing only the trigger would build on every push and deploy on
   none of them.

3. **Live constraint — `q` and `status` are mutually exclusive on `GET /builds`.** Passing both
   raises `ValidationError` (`squid/api/v1/builds.py:130-133`). Since `PUBLIC_SEARCH_STATUSES` is
   already fenced to `confirmed`, v1 ships **no status filter UI at all**, and the guided composer
   must never emit `status` alongside `q`. `page_size` also caps at 50, which bounds "load more".

4. **Confirmed non-issue — preview selection costs no extra queries.** `hydrate_builds` already
   loads full domain `Build` objects (`squid/api/v1/search.py:121`), so `render_urls` and
   `image_urls` are in hand when building each summary. Preview selection is pure field access, not
   an N+1.

## Decision: the contract changes are additive and small

- Extend `BuildTag` with its stable key. Today it carries only `display_name`
  (`squid/api/v1/schemas/builds.py:130-137`), which cannot be translated without guessing.
- Extend `BuildSummary` with:
  - `preview: {kind: "render" | "image", url: str} | None`
  - the version information cards need
  - opening and closing timing summaries
- Select the first valid HTTPS render, then the first valid HTTPS submitted image, then `None`.
- Extend `RecordDetail` with ordered `holder_builds: list[BuildSummary]`, retaining
  `holder_build_ids` on `RecordSummary` for existing consumers. A record whose holder builds cannot
  be loaded is an integrity failure and returns a problem response — never a record rendered without
  its holders, which would present a false claim.
- Apply finding 1's `BuildDetail` cleanup in the same commit.
- No database migration is required.

## Decision: generated SDK, server-only, drift-checked

Generate and commit the OpenAPI document and a full TypeScript fetch SDK with
`@hey-api/openapi-ts`, without query-library or schema-validation plugins.
[Hey API](https://heyapi.dev/) Generated code stays server-only and is excluded from coverage and
complexity metrics; CI regenerates and fails on drift.

Catalogue pages use a **per-request** SDK client with a five-second timeout, so mutable global
configuration cannot leak between SSR requests. Browser suggestions route through a narrow
same-origin Astro endpoint rather than exposing the API directly.

Every SSR call sends `Accept-Language` derived from the active route locale, so RFC 9457 problems
come back already localized by `squid/api/i18n.py`. The TypeScript dictionaries translate interface
copy, not the error vocabulary — duplicating `ErrorCode` in TypeScript would guarantee the two
drift.

## Decision: three spellings of one locale, one mapping helper

The route prefix is `/zh-cn`, the API tag is `zh-CN`, and the gettext directory is `zh_CN` — a
distinction `squid/core/i18n.py:28-29` already documents on the Python side. The web app gets a
single mapping helper and no ad-hoc `.replace()` calls at call sites.

Translate interface copy and known categories, statuses, units, search fields, and tag labels.
Preserve creator names and submitted titles and descriptions. Unknown taxonomy keys fall back to
their canonical API label. Dictionaries are typed so a missing translation fails typechecking.
Language switching preserves route, query, and cursor state.

## Decision: the design is a technical workshop, not a Minecraft pastiche

Deepslate backgrounds and raised surfaces, high-contrast redstone and copper accents, subtle
circuit/grid motifs without copying Minecraft assets, compact monospace specification labels, and
readable content typography. A temporary text wordmark and replaceable placeholder favicon sit
behind one brand component; v1 does not spend scope on a permanent logo.

Exactly two React islands:

- the guided/advanced search composer, with debounced abortable suggestions
- the keyboard-accessible media gallery

Navigation, cards, pagination, locale switching, detail layouts, and errors are server-rendered
Astro/HTML with progressive enhancement.

## Routes

`/`, `/builds`, `/builds/[id]`, `/records`, `/records/[id]`, `/search`, `/search/help`,
`/creators/[name]`, `/about`, the matching `/zh-cn/...` set, localized 404 and 503 pages,
`robots.txt`, and `sitemap.xml`.

- **Homepage** — technical search hero, latest builds, active-record highlights, community Discord
  CTA, secondary bot-invite link.
- **Build browse** — URL-addressable guided filters for creator, version, category/type, dimensions,
  timings, tags, and sort, with a toggle to the raw existing query syntax. No status filter; see
  finding 3.
- **Cursor pagination** — "load more" as real links that work without JavaScript. Cursor pages get a
  first-page canonical URL and `noindex,follow`.
- **Detail pages** — media, dimensions, timings, versions, patterns, restrictions, tags, creators,
  descriptions, schematics, downloads, external links.
- **`/creators/[name]`** — there is no creator endpoint; this is `GET /builds?q=creator:"…"`. Creator
  names are free-text IGNs, so the route owns quoting and escaping into the query grammar and is
  titled **"Builds by <name>"**. The API cannot distinguish an unknown creator from a creator with
  zero public builds, and the page does not pretend otherwise.
- **Media** — rendered directly at fixed aspect ratios, lazy, `referrerpolicy="no-referrer"`,
  HTTPS-only, with a branded broken-image fallback. Videos are linked, not embedded.
- **Metadata** — unique localized titles and descriptions, canonical URLs, `hreflang`, social
  metadata, structured data.
- **Sitemap** — a cached dynamic server endpoint paging through builds and records and emitting both
  locale variants. Astro's sitemap integration cannot discover dynamic SSR routes.
  [Astro sitemap limitation](https://docs.astro.build/en/guides/integrations-guide/sitemap/)

Runtime configuration is `API_BASE_URL`, `SITE_URL`, `DISCORD_COMMUNITY_URL`, and `BOT_INVITE_URL`.
Production mode rejects missing or placeholder values. No analytics or third-party telemetry in v1;
structured Node request/error logs only, revisited once hosting is chosen.

## Decision: the test matrix is proportionate, not maximal

The FYP's failure was breadth, and a plan that answers "475 tests, no thresholds" with five
one-worker browser jobs on every pull request repeats that mistake with the sign flipped. The gates
are split by how much signal they actually add per run.

**Vitest and Testing Library** — query composition, formatting, localization completeness, view
models, API-error mapping, and both islands. Enforce 90% lines/functions/statements and 85% branches
over handwritten testable code.

**Playwright** — the complete suite covers SSR/no-JavaScript navigation, both locales, guided and
advanced search, invalid syntax, cursors, build/record/creator pages, media fallbacks, 404/timeout/
503 states, metadata, and keyboard journeys.

- **On pull requests:** desktop Chromium and Mobile Safari.
- **On merge to `master`, and nightly:** all five projects — desktop Chromium, Firefox, WebKit,
  Mobile Chrome, Mobile Safari — each sharded into a separate one-worker job, per
  [Playwright's CI guidance](https://playwright.dev/docs/ci).

**Accessibility** — `@axe-core/playwright` against every representative layout, plus manual
verification of keyboard order, zoom/reflow, focus visibility, landmarks, and screen-reader naming.
Automated checks supplement manual review; they do not replace it. Target
[WCAG 2.2 AA](https://www.w3.org/TR/WCAG22/).

**Fixtures** — a small local HTTP fixture API runs alongside the production Astro build. Fixtures are
typechecked against generated SDK models. Media is served locally, dates frozen, animation disabled,
unexpected external requests blocked, and the same Linux fonts and browser image used in local
regeneration and CI.

**Visual regression** — stable Chromium desktop and mobile goldens for home, browse, build detail,
record detail, search errors, and localized layouts, compared at a maximum 0.1% differing-pixel
ratio. [Playwright visual comparisons](https://playwright.dev/docs/test-snapshots)

**Other CI gates** — Bun frozen lockfile, formatting and linting, Astro/TypeScript checks,
production build, SDK drift, dependency audit, and JavaScript bundle budgets. Lighthouse fixture runs
require at least 90 performance and 95 SEO/best-practices, with no ordinary page exceeding 100 KB
gzipped client JavaScript.

## Decision: documentation screenshots are on-demand and live outside `docs/`

Curated English desktop and Chinese mobile images for home, filtered builds, build detail, and
record detail.

They are **not** regenerated on every pull request. A catalogue this stable does not change its
screenshots often enough to justify committing a PNG set per PR, and doing so bloats history
permanently. Regeneration runs on a label or `workflow_dispatch`.

The output directory is **not** inside `docs/`. `docs/` is the zensical `docs_dir` built with
`--strict` into the Pages artifact, so screenshots placed there ship into the published site and
grow that artifact on every regeneration. They land outside the docs tree, and any docs page that
wants one references it deliberately.

The security shape of the workflow is unchanged and non-negotiable:

- PR code runs only in a read-only workflow that uploads the regenerated PNG set.
- A separate trusted workflow validates an exact path allowlist, PNG signatures, total size,
  same-repository origin, and an unchanged PR head SHA before committing with
  `docs: regenerate website screenshots`.
- Fork PRs receive replacement artifacts and fail with update instructions; CI never receives
  authority to push to a fork.
- The trusted job never executes PR-provided scripts, so untrusted code cannot reach a write token.

## Phases

1. **API contract.** `BuildTag` key; `BuildSummary` preview, versions, timings; `BuildDetail`
   de-duplication per finding 1; `RecordDetail.holder_builds`. Register with the schemathesis
   contract harness. Commit the regenerated OpenAPI document.
2. **Web foundation.** Scaffold `/web` — Astro, React, strict TypeScript, Bun, custom CSS tokens,
   standalone [`@astrojs/node` adapter](https://docs.astro.build/en/guides/integrations-guide/node/).
   Generated SDK, per-request client, locale mapping helper, typed dictionaries, brand component.
3. **Catalogue pages.** Routes, both islands, cursor pagination, media handling, metadata, sitemap
   endpoint, problem-response mapping.
4. **Testing and docs automation.** Vitest, Playwright, axe, fixtures, visual goldens, bundle and
   Lighthouse budgets, the screenshot workflows — and the four `main` → `master` fixes from
   finding 2.

## Acceptance

- Every catalogue route returns meaningful server-rendered English or Chinese HTML and stays
  navigable without JavaScript.
- Search and filter state is shareable, API failures are explicit, remote media cannot collapse
  layouts, and representative pages pass the browser, accessibility, visual, and performance gates.
- An API failure never renders as an empty catalogue.

## Assumptions and deferred work

- The site is deployable as a standalone Node server, but domain, reverse proxy, CDN caching,
  production secrets, telemetry, and the final hosting adapter are deployment decisions this plan
  does not make.
- Hosting credentials and deployment automation stay out of v1.
- Existing unrelated work on `schematics-phase-0` must be preserved; implementation starts from a
  clean dedicated branch or worktree.
- Implementation requires roughly 5 GB of free disk for Bun dependencies and Playwright browsers.
  The environment had 423 MB free at planning time; the operator is provisioning 10 GB before
  Phase 2. Phase 1 is pure Python and can proceed under the current constraint.
- The FYP findings are based on source and report inspection. Its suite could not be reinstalled and
  rerun under the planning-time disk constraint, so its test counts are as-reported, not as-verified.
