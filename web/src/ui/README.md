# Catalogue design system

Every component is Svelte, one file each, grouped by folder. Astro pages render them
statically or hydrate them as islands. Open `/design-system` for the tokens and one page of
stories per folder; `/zh-cn/design-system` shows them in Simplified Chinese.

## Composing a screen

| Need                                                      | Use                                                                              |
| --------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Page width and gutter, a skip link                        | `layout/Container`, `content/SkipLink`                                           |
| Vertical rhythm, wrapping rows, title and actions, grids  | `layout/Layout` with `stack`, `cluster`, `split`, `grid` (`min`), `center`       |
| Page title, breadcrumbs, meta, actions                    | `patterns/PageHeader` (the page's only `h1`)                                     |
| Counts of related records under the title, as links       | `patterns/RelatedLinks`; same items in every state, zeros included               |
| A titled part of a page                                   | `patterns/Section`, with `surface` for a panel                                   |
| The site header                                           | `patterns/NavBar`; its links collapse into a popover when narrow                 |
| A sidebar of filters or section navigation                | `patterns/SplitLayout`, `collapse="sheet"` to hide it behind a button            |
| Searching and filtering a listing                         | `patterns/SearchBox`, `FilterPanel`, `ActiveFilters`: GET forms and links        |
| The bar above results, and the results themselves         | `patterns/ResultsToolbar`, `patterns/ResultsView` (loading, empty, error)        |
| Confirming an irreversible action                         | `patterns/ConfirmDialog`, with `fallbackHref` to a confirmation page             |
| Settings that save on change                              | `patterns/SettingsList` of `Switch`es with per-row `SaveStatus`                  |
| Draft files, secrets shown once, multi-step flows         | `patterns/MediaUploader`, `OneTimeSecret`, `StepFlow`                            |
| The outcome after a POST redirect                         | `patterns/FlashNotice` in the `PageHeader` notice slot                           |
| Headings, body text, links, code                          | `content/Heading`, `Text`, `Link`, `Code`, `CodeBlock`, `Prose` for Markdown     |
| Text only screen readers need                             | `content/VisuallyHidden`                                                         |
| Showing a date                                            | `content/Timestamp`; format the label in a message                               |
| Two or three facts of one kind, such as count and version | `content/MetaList`; facts with other roles go elsewhere                          |
| Icons                                                     | `content/Icon` wrapping a lucide icon; decorative unless labelled                |
| Commands and links that look like them                    | `actions/Button`, with `href` for a link and `pending` while saving              |
| Copying a value                                           | `actions/CopyButton` beside the value in a selectable `Code`                     |
| State and tags                                            | `feedback/Badge` for a state such as pending, `feedback/Chip` for tags           |
| Messages, outcomes, failures                              | `feedback/Notice`; choose its `role` by how it should be announced               |
| Island messages with no visible home, such as new counts  | `announce()` from `feedback/announcer`, read by the layout's `Announcer`         |
| Loading, empty, failed regions                            | `feedback/Skeleton`, `EmptyState`, `ErrorState`; `Spinner` beside one line       |
| Progress and levels                                       | `feedback/Progress`, with `kind="meter"` for votes or completeness               |
| One item as a card                                        | `data/Card`; with `title` and `href` the whole card links                        |
| Rows of items                                             | `data/List` and `ListItem`                                                       |
| Labelled values and headline numbers                      | `data/DescriptionList`, `data/StatGroup`                                         |
| Images and people                                         | `data/Image` (fixed frame with fallback), `data/Avatar`                          |
| Media with thumbnails and a lightbox                      | `data/Gallery`                                                                   |
| Tabular data, sorted by the server, with grouped rows     | `table/DataTable`; sort, `hide=` and `open=` are links                           |
| Values over time or categories                            | `chart/SeriesChart` (line, step, bar, stacked bar)                               |
| Values over runs of widths                                | `chart/RangeChart`                                                               |
| Ranked shares, top N plus other                           | `chart/DistributionBar`                                                          |
| Another kind of chart                                     | `chart/Chart`, the frame with caption, legend, and data table                    |
| Events and changes                                        | `data/Timeline`, `data/Diff`                                                     |
| Hidden detail                                             | `data/Disclosure`                                                                |
| Moving between pages and views                            | `navigation/Breadcrumbs`, `Tabs`, `SegmentedControl`, `NavList`, `Pagination`    |
| In-page alternatives without a URL                        | `navigation/Tabs` with `mode="panel"`                                            |
| A choice among a few options in a form, or saved at once  | `navigation/SegmentedControl` with `mode="radio"` or `mode="submit"`             |
| Text, numbers, choices from a short list                  | `forms/TextField`, `TextareaField`, `SelectField`, `ChoiceGroup`                 |
| Booleans                                                  | `forms/CheckField` saved with the form, `Switch` applied at once                 |
| Related fields, ranges, units, one-time codes             | `forms/FieldGroup`, `RangeField`, `UnitField`, `CodeEntry`                       |
| Entering dates, times, and spans of dates                 | `forms/DateField` (`time` adds the time), `TimeField`, `DateRangeField`          |
| Suggestions or picking from a long set                    | `forms/Autocomplete` for free text, `Combobox` for values from a set             |
| Repeatable rows and files                                 | `forms/ListField`, `FileDrop`                                                    |
| Ending a form and reporting its state                     | `forms/FormActions`, `ErrorSummary` after a failed submit, `feedback/SaveStatus` |
| A modal task, or a panel from a screen edge               | `overlays/Dialog`, `overlays/Sheet`; give triggers a `fallbackHref`              |
| Actions or links behind a button                          | `overlays/Menu`; `nojs` for links and forms that work without JavaScript         |
| Help beside a control, names of icon buttons              | `overlays/Popover`, `overlays/Tooltip` (never the only source of information)    |
| Filters in the URL, focus after a change, island fetching | `lib/url-state`, `lib/focus`, `lib/query-client`                                 |

Features and layouts build on these: lint rejects raw `<button>`, `<input>`, `<select>`,
`<textarea>`, `<table>`, `<dialog>`, `<img>` and `<style>` there, and imports of `bits-ui`,
`layerchart`, `@tanstack/svelte-table`, `@internationalized/date` or a folder's `styles.ts`. Components take their text through props
and know nothing about builds, records, or the API. A component's own text, such as button
names and status messages, arrives in one `labels` object; text that depends on a value is a
function there, such as `labels.count(n)`, so plurals stay in the catalogs. Add one here only when two features need
it; see `web/AGENTS.md`.

## Styles

Components style themselves with Tailwind utilities from `app.css`, which exposes the theme
tokens in `themes.css`. Merge a caller's `class` with `cn`, and accept it only for properties
the component leaves open, such as margins. A style used by one component lives in it; a
class string shared by a folder lives in that folder's `styles.ts`. When a selector does the
work, such as styling slotted content, vendor pseudo-elements, or a library's markup, write a
scoped `<style>` block instead of a chain of arbitrary variants, hooked on an element or a
`data-` attribute (lint rejects class names that are not utilities). Its rules are unlayered,
so they beat utilities: never style there what a caller's `class` may set. Stylelint holds
blocks to the same tokens (`var(--color-*)`, `var(--z-*)`, `var(--duration-*)`), and only
`src/ui` may have them. Durations (`duration-fast`, `-normal`, `-slow`) and stacking
(`z-sticky` up to `z-skiplink`) come from `app.css`. Arbitrary values are allowed only here, and only for grid tracks, transition properties, em sizes, and
viewport-bounded sizes; `scripts/lint/arbitrary-values.ts` holds the list. Use sentence case for headings, labels, and badges. Numbers and measurements stay in the
sans face (`numeric` for tabular figures); monospace is only for `Code`. A badge marks one
state. Give every other fact a place by its role (eyebrow, `DescriptionList`, the detail page)
and keep `MetaList` for two or three facts of one kind.

Layouts are fluid rather than tuned to a few screen sizes: `Layout` grids and wrapping rows,
`minmax(0, 1fr)` tracks, and `clamp()` gutters and panel padding (`px-gutter`, `p-panel`).
When a component must change shape, it queries its own width: `@container` on its root and
variants such as `@max-lg:hidden` on its children, as in `Card`, `Breadcrumbs`, and
`Pagination`. Lint rejects viewport breakpoints (`md:`) outside page layouts, because one
component can sit in a page, a card, or a dialog. A query container cannot take its width
from its content, so give it a parent that sets the width. Page regions such as `Container`
are not query containers: containment makes each a stacking context, which would trap the
header's menus beneath the main content. `tests/review/reflow.spec.ts` checks every width
from 320px to 1600px.

Charts take series data, draw static SVG on the server and ship the same data as a table;
`interactive` adds a tooltip and arrow keys once hydrated. Series colors are the `chart-*`
tokens, assigned in order through a `tone` and never used for text. Astro cannot write
snippets, and functions cannot cross into an island, so a Svelte feature component wraps
components that take them, such as `DataTable`, the charts, and fields with label functions.

Motion only explains a change: an overlay entering or leaving, content growing open, an
indicator moving to the new choice, content replacing a skeleton. It uses the duration and
`ease-*` tokens behind `motion-safe:` or `prefers-reduced-motion: no-preference`, and
`app.css` turns every transition off under reduced motion. bits-ui keeps an overlay mounted
until its transitions end and marks the exit `data-ending-style`, so give it an exit look
that mirrors the `starting:` one; a native popover transitions `display` and `overlay`
discretely instead. `@starting-style` also fires on page load, so a fade-in for content that
arrives later (`ResultsView`, `DataTable` rows) is added only after mount. The `motion` page
of the workbench shows each of these.

Astro wraps slot content passed to a Svelte component in an `astro-static-slot` element,
which `app.css` displays as `contents`. The wrapper still breaks list and table structure,
so components that render list items or rows take data, not slotted rows.

## Stories and review

Each folder has a stories page, `src/workbench/stories/<Folder>Stories.svelte`, that shows
every variant and state through `Story` (`source` is the component's path, such as
`feedback/Badge`); the `motion` page gathers motion across folders. `tests/unit/workbench-stories.test.ts` fails when a component has no story.
To see a change, run `bun run dev` and open its folder's page under `/design-system`.
`tests/review/workbench.spec.ts` checks every page in both themes on desktop and mobile with
axe and an overflow check; `/design-system-trial` exercises a complete draft. Screenshots are
not committed: CI compares each pull request's with master's and lists the differences in the
job summary and the gallery artifact, without failing. `bun run test:compatibility` runs the
trial in Firefox and WebKit.
