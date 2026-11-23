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
| Icons                                                    | `content/Icon` wrapping a lucide icon; decorative unless labelled             |
| Commands and links that look like them                   | `actions/Button`, with `href` for a link and `pending` while saving           |
| Metadata and tags                                        | `feedback/Badge` for static labels, `feedback/Chip` for clickable tags        |
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
imports. Use sentence case for headings, labels, and badges, and monospace only for
technical values.

Astro wraps slot content passed to a Svelte component in an `astro-static-slot` element,
which `app.css` displays as `contents`. The wrapper still breaks list and table structure,
so components that render list items or rows take data, not slotted rows.

## Stories and review

Each folder has a stories page, `src/workbench/stories/<Folder>Stories.svelte`, that shows
every variant and state through `Story` (`source` is the component's path, such as
`feedback/Badge`). `tests/unit/workbench-stories.test.ts` fails when a component has no story.
`tests/review/workbench.spec.ts` captures every page in both themes on desktop and mobile
and runs axe; `/design-system-trial` exercises a complete draft. After an intentional visual
change, run `bun run test:review:update`, inspect the screenshots, and rerun
`bun run test:review` against them. `bun run test:compatibility` runs the trial in Firefox
and WebKit.
