# Design foundations

Open `/design-system` for the component workbench. Its `lang=en|zh-CN` and
`theme=light|dark` parameters make each review scenario directly addressable.
The page is excluded from indexing and uses local fixture data, not live builds.

`tokens.css` owns semantic colours, spacing, typography, motion, and layering.
`base.css` owns document defaults; `components.css` owns primitive `sq-` styles.
Shell styles live in `layouts/workbench-layout.css`, workbench examples in
`features/workbench/workbench.css`, and build previews beside their component
in `features/builds/components/`. Keep new feature styles with their feature.
Astro and React controls share the variants in `variants.ts`. Keep feature rules
and API calls outside `ui/`. Use Radix for interactive behavior and Lucide for
icons; icon-only buttons need accessible names. The layout serves Inter,
JetBrains Mono, and Noto Sans SC from installed Fontsource packages so fonts do
not depend on a third-party request during rendering or screenshot capture.

The workbench layout is the reference for the replacement website. Existing
catalogue routes still use the old layout until their workflows are rebuilt.
The illustrations in `public/fixtures` are schematic examples, not real builds.

## Localization

All website messages live in `src/locales/en.ftl` and `zh-CN.ftl`. Use Fluent
variables and selectors for interpolation and pluralization. Call `translate`
with a message key and variables in either Astro or React; do not branch on
locale to embed translated prose in components. Pass the locale explicitly to
islands so server and browser render the same language. React and Astro escape
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
