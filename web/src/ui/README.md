# Catalogue design system

Every component is Svelte, one file each, grouped by folder. Astro pages render them
statically or hydrate them as islands. Open `/design-system` for the tokens and one page of
stories per folder; `/zh-cn/design-system` shows them in Simplified Chinese.

## Composing a screen

| Need                                                     | Use                                                                           |
| -------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Page width and gutter                                    | `layout/Container`                                                            |
| Vertical rhythm, wrapping rows, title and actions, grids | `layout/Layout` with `stack`, `cluster`, `split`, `grid` (`min`), `center`    |
| Page title, breadcrumbs, meta, actions                   | `patterns/PageHeader` (the page's only `h1`)                                  |
| A titled part of a page                                  | `patterns/Section`, with `surface` for a panel                                |
| Headings, body text, links, code                         | `content/Heading`, `Text`, `Link`, `Code`, `Prose` for rendered Markdown      |
| Dates                                                    | `content/Timestamp`; format the label in a message                            |
| Facts about an item: version, count, date                | `content/MetaList`, separated by space, not punctuation                       |
| Icons                                                    | `content/Icon` wrapping a lucide icon; decorative unless labelled             |
| Commands and links that look like them                   | `actions/Button`, with `href` for a link and `pending` while saving           |
| State and tags                                           | `feedback/Badge` for a state such as pending, `feedback/Chip` for tags        |
| Messages, outcomes, failures                             | `feedback/Notice`; choose its `role` by how it should be announced            |
| Loading, empty, failed regions                           | `feedback/Skeleton`, `EmptyState`, `ErrorState`; `Spinner` beside one line    |
| Progress and levels                                      | `feedback/Progress`, with `kind="meter"` for votes or completeness            |
| One item as a card                                       | `data/Card`; with `title` and `href` the whole card links                     |
| Rows of items                                            | `data/List` and `ListItem`                                                    |
| Labelled values and headline numbers                     | `data/DescriptionList`, `data/StatGroup`                                      |
| Images and people                                        | `data/Image` (fixed frame with fallback), `data/Avatar`                       |
| Events and changes                                       | `data/Timeline`, `data/Diff`                                                  |
| Hidden detail                                            | `data/Disclosure`                                                             |
| Moving between pages and views                           | `navigation/Breadcrumbs`, `Tabs`, `SegmentedControl`, `NavList`, `Pagination` |
| User input                                               | `forms/Field`, `SelectField`, `TextareaField`, `CheckField`, `FieldGroup`     |
| Suggestions, files, modal forms                          | `forms/Autocomplete`, `forms/FilePicker`, `overlays/Dialog`                   |

Shared components take their text through props, from `@/locales/messages.gen`. Keep API
calls, validation rules, and translations in the feature. When several features need the
same presentation, add a component here with a story; anything that knows about builds,
records, or the API belongs in `src/features`.

## Styles

Components style themselves with Tailwind utilities from `app.css`, which exposes the theme
tokens in `themes.css`. Merge a caller's `class` with `cn`, and accept it only for properties
the component leaves open, such as margins. A style used by one component lives in it; a
style shared by a folder lives in that folder's `styles.ts`, which nothing outside `src/ui`
imports. Use sentence case for headings, labels, and badges. Numbers and measurements stay in the
sans face (`numeric` for tabular figures); monospace is only for `Code`. A badge marks one
state; categories and versions are plain text in the eyebrow or a muted meta line.

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

Astro wraps slot content passed to a Svelte component in an `astro-static-slot` element,
which `app.css` displays as `contents`. The wrapper still breaks list and table structure,
so components that render list items or rows take data, not slotted rows.

## Stories and review

Each folder has a stories page, `src/workbench/stories/<Folder>Stories.svelte`, that shows
every variant and state through `Story` (`source` is the component's path, such as
`feedback/Badge`). `tests/unit/workbench-stories.test.ts` fails when a component has no story.
To see a change, run `bun run dev` and open its folder's page under `/design-system`.
`tests/review/workbench.spec.ts` checks every page in both themes on desktop and mobile with
axe and an overflow check; `/design-system-trial` exercises a complete draft. Screenshots are
not committed: CI compares each pull request's with master's and lists the differences in the
job summary and the gallery artifact, without failing. `bun run test:compatibility` runs the
trial in Firefox and WebKit.
