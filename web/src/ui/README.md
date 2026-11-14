# Design foundations

Open `/design-system` for the component workbench. Its `lang=en|zh-CN` and
`theme=light|dark` parameters make each review scenario directly addressable.
The page is excluded from indexing and uses local fixture data, not live builds.

`tokens.css` owns semantic colours, spacing, typography, motion, and layering.
`base.css` owns document defaults; `components.css` owns primitive `sq-` styles.
Shell styles live in `layouts/workbench-layout.css`, workbench examples in
`features/workbench/workbench.css`, and build previews beside their component
in `features/builds/components/`. Keep new feature styles with their feature.
Astro, Svelte, and the React comparison controls share the variants in `variants.ts`. Keep feature rules
and API calls outside `ui/`. Use Bits UI for new interactive controls and Lucide for
icons; icon-only buttons need accessible names. The layout serves Inter,
JetBrains Mono, and Noto Sans SC from installed Fontsource packages so fonts do
not depend on a third-party request during rendering or screenshot capture.

The workbench layout is the reference for the replacement website. Existing
catalogue routes still use the old layout until their workflows are rebuilt.
The illustrations in `public/fixtures` are schematic examples, not real builds.

## Localization

All website messages live in `src/locales/en.ftl` and `zh-CN.ftl`. Use Fluent
variables and selectors for interpolation and pluralization. Call `translate`
with a message key and variables in Astro, Svelte, or legacy React; do not branch on
locale to embed translated prose in components. Pass the locale explicitly to
islands so server and browser render the same language. The rendering frameworks escape
formatted text; catalogs do not contain HTML.

Run `bun run i18n:generate` after changing message IDs. The generated keys retain
the existing dotted API (`builds.title` maps to Fluent's `builds-title`). Build
and type checks reject malformed catalogs, duplicate or missing messages,
variable mismatches, and stale generated keys. The runtime falls back to English
for missing or unformattable translated messages, and throws if neither locale
can format the requested message. Catalog validation keeps that fallback from
hiding incomplete translations during development.

## Review

From `web/`, run `bun run build`, then `bun run test:review`. Install the pinned
browser first with `bunx playwright install chromium`. The tests start the app
and fixture API. They check keyboard behavior, accessibility, server-rendered
content, and eight visual combinations of language, theme, and viewport.

`bun run test:compatibility` runs the framework comparison in Firefox and WebKit;
install them with `bunx playwright install --with-deps firefox webkit`. These
checks also run in CI, with reports under `playwright-report/compatibility/`.
Draft-form axe scans run in Chromium because Playwright's Firefox revision 1572
crashes during the scan in both implementations. Firefox still runs the complete
workflow and dialog accessibility checks. Remove that restriction after checking
a browser upgrade. Local WebKit validation requires its Linux system libraries.

Open `review-gallery/index.html` for screenshots and successful workflow videos;
`playwright-report/review/index.html` contains detailed failures and traces.
The website review workflow uploads both on relevant pull requests. Gallery
assets and videos are generated artifacts; only approved screenshot baselines
are tracked in Git.

Use `bun run test:review:update` for intentional visual changes, inspect the
resulting PNG changes, then rerun `bun run test:review`. Generate and compare
baselines with the same OS, fonts, and pinned browser. Do not approve differences
merely to clear a failing check. Add fixtures for new states before building
features that depend on them.

## Island framework decision

Use Svelte 5 for new islands. Astro still owns routes, page loading, metadata,
and static composition. Keep validators, API adapters, and Fluent resources
framework-neutral. Existing React production islands remain until their feature
is rebuilt; avoid loading both runtimes on the same page during that migration.

`/design-system` and `/design-system-trial` default to Svelte. Add
`?framework=react` for the reference implementation; both routes accept
`lang=zh-CN`, and the trial accepts `scenario=retry`. These are local simulations,
not production submission endpoints. The trial includes conditional fields,
version filtering, upload progress, cancellation, validation, and retry state.

The same Chromium desktop/mobile checks cover both implementations, in both
languages, including SSR without JavaScript, keyboard focus, and accessibility.
The measured initial JavaScript (gzip, including renderer and shared imports)
was approximately 57 KiB for the Svelte workbench versus 104 KiB for React, and
43 KiB versus 91 KiB for the draft trial. Run `bun run build` followed by
`bun run check:bundle` to reproduce measurements against the current lockfile.
These are transfer-size measurements, not CPU or real-network benchmarks.

Bindings and derived state make the tested local forms easier to follow, while
Bits UI supplies the keyboard/focus behavior our components need. The tradeoff
is additional Svelte tooling and slower combined builds while React coexists.
Browser interaction tests remain authoritative for focus and layout; jsdom unit
tests also cover binding, composition, validation, and cancellation. Keep the
React trial as a migration reference, not a second API for new product features.
Remove the comparison implementation after the first production feature proves
the selected patterns, retaining these measurements and the shared scenarios.
