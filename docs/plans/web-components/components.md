# Component spec

The `src/ui` primitives and patterns, the feature components built from them, and the interaction patterns every screen follows. Screen IDs refer to [screens.md](screens.md).

## 0. Problems in the current code (fix or remove)

**Tokens and styling**

1. **Inputs fail non-text contrast.** `inputClass`, `FilePicker`, `LanguageSelector` and cards use `border-line`. In light mode that is about 1.3:1 against `surface`, and WCAG 1.4.11 needs 3:1 for control boundaries. Add `line-strong` and use it for control borders. `line` stays for dividers and cards.
2. **Danger is tied to the brand accent.** `noticeStyles.danger` uses `bg-accent-soft`. Add `danger-soft`. The `info` tone is a grey `border-line` with no info color, so add `info` and `info-soft`.
3. **Arbitrary values in `src/ui` and layouts.** Badge uses `text-[0.6875rem]` and `font-[650]`. Field hints and errors and Autocomplete use `text-[0.8125rem]`. `kickerClass` uses `leading-[normal]`. Buttons use `duration-140`. The header uses `font-[750]` and `shadow-[0_2px_currentColor]`. LanguageSelector uses `rounded-[0.25rem]`. The layout uses `w-[calc(100%-4rem)]` and `max-[65rem]:`. The workbench uses `max-w-[100rem]` and `grid-cols-[13rem_…]`. The Dialog uses `w-[min(30rem,…)]`. Replace each with a token from §2.4.
4. **Two container definitions disagree.** `layoutStyles({pattern:"container"})` uses `px-[clamp(1rem,4vw,3rem)]`. `CatalogueLayout` uses `w-[calc(100%-4rem)]` and `max-sm:w-[calc(100%-2rem)]`, three times. Replace both with one `Container` and a `--spacing-gutter` token.
5. **Ad-hoc z-index.** Autocomplete uses `z-10`, LanguageSelector `z-20`, Dialog `z-50`, and the skip link `z-100`, with no ladder. Define one (§2.4).
6. **Raw hex colors.** `CatalogueLayout` passes `themeColor="#f5f5f4"` and `DocumentHead` defaults to `#090b0c`. Derive both from `themes.ts`, adding a `themeColor` per theme.
7. **Inconsistent radii.** `panelClass` uses `rounded-lg` while `BuildPreview` cards use `rounded-md`. Cards and panels both become `md`; `lg` is for dialogs only.
8. **The `h1` base style forces display size on every page.** It uses `clamp(2rem,5vw,3.5rem)` and `-0.045em` tracking, which is also too tight for CJK. Move sizes to a `Heading` component; leave the base layer at a neutral size. Drop negative tracking under `:lang(zh)`.

**Components**

9. **Field errors use `role="alert"`.** This is in `FieldFrame` and `FieldGroup`. A server-rendered page with three errors fires three alerts, and every re-render re-announces them. Make field errors plain text linked by `aria-describedby`. An `ErrorSummary` (§2.2) owns the announcement and focus.
10. **Autocomplete re-implements the field frame.** It has no hint or error and doesn't use `FieldFrame`. Each option sets `aria-label={item.label}`, which hides the description from screen readers. `minLength` means "minimum query length", which clashes with the HTML attribute of the same name. Rebuild it on `FieldFrame`, rename the prop to `minQueryLength`, and attach descriptions with `aria-describedby`.
11. **Dialog is too rigid.** It always renders its own trigger, styled primary by default. It can't be opened from code, requires a description, and has a single size. That blocks confirm dialogs, controlled dialogs and sheets. See §2.1 Dialog.
12. **Notice and Badge exist twice.** `Badge` is Astro-only and `Notice` exists in both frameworks. `src/ui/astro/*` goes away (decided).
13. **Placeholder pages link to the workbench.** `PlaceholderPage` puts `/design-system` on production pages. Remove that link. Placeholders link home only.
14. **`BuildPreview` sits in the wrong layer.** It is a generic card living in `features/`. The workbench calls it with duplicate `workbench*` messages (`workbenchDimensions`, `workbenchOpening`, `workbenchNoPreviewAvailable`). The workbench should render the production `BuildCard` with fixtures, and those messages should be deleted.
15. **`BuildPreview` contains raw text and inline handlers.** It has the `◇` glyph and an inline `onerror="this.hidden=true"` handler. `BuildCard` has a literal `"?"` and `"—"`. LanguageSelector has `⌄` and `✓`. The header has `↗`. Replace these with lucide icons and messages. The `Image` primitive owns the fallback.
16. **`SummaryCard` uses arbitrary descendant selectors.** `[&_a]:no-underline [&_a:hover]:underline` styles links it doesn't own. Use `Card` with a stretched link.
17. **Pagination ignores `total` and lives in `features/catalogue`.** Users never see how many results there are. It also renders an empty `<span />` as a spacer. It moves to `src/ui` (§2.1).
18. **Hard-coded section IDs.** `RecordDetail` uses `id="record-holders"`, and `Panel` requires callers to pass an id. Generate ids with `$props.id()`.
19. **Margins baked into exported constants.** `kickerClass` includes `mb-3`. Remove all public style constants: `buttonStyles`, `layoutStyles`, `noticeStyles`, `inputClass`, `fieldClass`, `panelClass`, `kickerClass` and `mutedClass` stay private to `src/ui`, and lint bans them outside it.
20. **Raw brand text.** `WorkbenchLayout` has a literal `Redstone Squid` in `<title>` and in the header. `CatalogueLayout` draws an "R" logo square. Use `m.siteName()` and the favicon SVG.
21. **The header has no account entry.** There is no sign-in, user menu, notifications link or staff link. The mobile header wraps into three rows instead of collapsing.
22. **Workbench chrome duplicates the site shell.** It has its own skip link and sidebar with arbitrary values. Rebuild it from `SkipLink`, `SplitLayout` and `NavList`.
23. **`Progress` wraps `<progress>` in a `<label>`.** That works, but `Meter` and `Progress` should share one API (§2.1).
24. **`FilePicker` has no hint or error, and no drop target.** It is replaced by `FileDrop`.

---

## 2. `src/ui` component spec

### Conventions that apply to every component

- **Svelte 5 only, one file each.** Each component has a story in the workbench using the production component.
- **Text comes in through props.** Labels are plain strings, not message functions, so Astro can pass them to an island; island props must be serializable. Every accessible name that the component itself would otherwise supply (close, expand, sort, copy, remove) is a required prop, or comes from a `labels` object prop.
- **`class` is accepted only on layout-neutral roots,** for grid placement and width. Variants own every other property.
- **One control size per row.** `md` (44px) in forms and among a page's actions; `sm` (36px) beside text and in dense places: tables, cards, toolbars inside a panel. Controls inside a field's box (clear, toggle, chips) belong to the field: they fill its height or use `Chip size="sm"`, so the field stays as tall as a plain one.
- **Spacing comes from components.** Screens (`src/features`, `src/pages`) space their parts with `Layout`'s `gap`, `Section` and component padding; lint bans gap, margin and padding utilities there, so every screen keeps the same rhythm. Page shells in `src/layouts` set their own.
- **Ids come from `$props.id()`.** Callers never pass ids unless they need to link to them.
- **Astro can pass children (default slot) and named slots without arguments.** It cannot pass snippets that take arguments. A component whose rendering needs a parameterized snippet (table cells, combobox items) is therefore wrapped in a Svelte feature component, and Astro pages pass only data to it.
- **States vocabulary:** `disabled` means the user could enable it on this screen. `pending` means a mutation is in flight. `loading` means data is being fetched. Errors are text props.
- **No-JS:** a statically rendered component must produce working HTML: links, GET or POST forms, `<details>`.
- **Astro slot wrappers:** Astro wraps slot content passed to a Svelte component in `<astro-static-slot>` (or `<astro-slot>` in islands). Astro only ships the `display: contents` rule for those elements on pages that hydrate an island, so `app.css` defines it. The wrapper still breaks table parsing and `ul > li` structure, so components that render rows or list items take data, not slotted rows.

### 2.1 Primitives (domain-free)

#### Layout

**`Layout`**

- _Purpose:_ arrange children. Replaces `layoutStyles` and Astro `Layout`.
- _API:_ `as` (div, section, article, header, footer, nav, ul, ol, li, main, aside); `pattern` of `stack | cluster | split | grid | center`.
  - `split`: the first child grows and the last is pushed to the end, as in a title with actions.
  - `center`: content centered horizontally and vertically.
  - `gap` of `none | 2xs | xs | sm | md | lg | xl`. Each pattern has a default gap.
  - `align` and `justify` for cluster and split.
  - `min` (grid minimum column width) of `card | card-wide | tile`, mapped to tokens.
  - `wrap` (cluster only).
- _A11y:_ none of its own. It uses semantic elements; use `as="ul"` for lists of cards.
- _Responsive:_ grid uses auto-fit with the `min` tokens. `split` wraps below its content width.
- _Users:_ every screen.

**`Container`**

- _Purpose:_ page-width wrapper with the gutter token.
- _API:_ `width` of `content | prose | form | wide`.
- _Users:_ layouts, P14, S2, A2.

**`SplitLayout`** (pattern)

- _Purpose:_ a sidebar plus main area.
- _API:_ `sidebar` snippet, `children`, `sidebarLabel`, `side` (start or end), `sticky`, `collapse` of `sheet | stack`.
  - `collapse="sheet"` puts the sidebar in a `Sheet` on mobile, opened by its `trigger` (an `OverlayTrigger`, named `sidebarLabel` by default).
- _Users:_ P2, P4, P7, P12 (filters), S2 (section nav), A2–A7 (account nav), X1–X8 (admin nav), and the workbench.

#### Text and content

**`Heading`**

- _API:_ `level` (1–6, which sets the semantic tag), `size` (`display | xl | lg | md | sm`, defaulting from the level), `eyebrow?` (short text above, replacing `kickerClass`), `id?`, `tabindexFocus?` (adds `tabindex=-1` so focus can be moved to it after navigation).
- _Users:_ every screen.

**`Text`**

- _Purpose:_ body text with a tone.
- _API:_ `as` (p, span, div), `size` (`sm | md`), `tone` (`default | muted | danger | success`), `numeric?` (tabular figures for numbers that line up or update), `truncate?` (`lines: 1 | 2 | 3`).
- _Users:_ everywhere. Replaces `mutedClass`.

**`MetaList`**

- _Purpose:_ two or three facts of the same kind, such as a holder count and a version, separated by dots that assistive technology skips.
- _Rule:_ give each fact a role before reaching for it. Classification goes in the eyebrow or title, the main number gets weight, a state is a `Badge`, labelled detail is a `DescriptionList`, and housekeeping such as "computed on" belongs on the detail page. Show a fact only when it tells the reader something, such as a version scope that narrows the record.
- _Users:_ record and build cards, search results, list rows.

**`Link`**

- _Purpose:_ inline or standalone link.
- _API:_ `href`, `variant` (`inline | standalone | subtle`), `external?` (adds an icon and an accessible "opens externally" suffix from the `externalLabel` prop, plus `rel="noreferrer"`), `current?` (sets `aria-current`).
- _Users:_ everywhere.

**`Prose`**

- _Purpose:_ renders sanitized HTML or Markdown output with typographic styles: headings with anchor links, lists, tables, code, blockquotes.
- _API:_ `html` (already sanitized server-side; the prop is typed as a branded `SafeHtml`), `size`.
- _A11y:_ headings keep their levels. The anchor links get `aria-label` from `anchorLabel`.
- _Users:_ P14, P5 (description), P13 (bio), A1 (privacy notice), P3.

**`Code`**

- _Purpose:_ inline code or a code block.
- _API:_ `block?`, `wrap?`, `maxLines?` (collapses into a `Disclosure` with `expandLabel`).
- _Users:_ P3, X2 (traceback), P5 (expansion expressions).

**`Kbd`**: merged into `Code` as `variant="key"` (P3 shortcuts).

**`Timestamp`**

- _API:_ `datetime` (ISO string), `label` (formatted text from a message), `title?` (absolute date on hover).
- _Renders:_ `<time datetime>`. Formatting stays in the messages.
- _Users:_ P5, P6, P8, A4, A5, A6, S1, X2.

**`Icon`**

- _Purpose:_ wraps lucide.
- _API:_ `icon` (component), `size` (`sm | md | lg`), `label?`. With a label it gets `role=img` and `aria-label`; otherwise it is `aria-hidden`.
- _Users:_ everywhere.

**`VisuallyHidden`** and **`SkipLink`**: an sr-only utility component, and a skip link (`href`, `label`).

- _Users:_ layouts and table captions.

#### Actions

**`Button`** (merges `LinkButton`)

- _API:_ `variant` (`primary | secondary | quiet | danger`), `size` (`sm | md`), `href?` (renders `<a>`), `type`, `icon?`, `iconEnd?`, `iconOnly?` (a `label` is then required and becomes `aria-label`), `pending?` plus `pendingLabel`, `disabled?`, `fullWidth?`, `external?`, `form?`, `name`/`value` (for multi-action POST forms).
- _States:_
  - `pending`: spinner replaces the start icon, width is preserved, the button gets `aria-disabled="true"` and rejects clicks, and `pendingLabel` goes into a visually hidden live region.
  - `disabled`: native disabled on buttons; links in a disabled state render as `<span aria-disabled>`.
- _A11y:_ native semantics, minimum 44 px target (`size=sm` keeps 36 px with a 44 px hit area through padding), visible focus ring.
- _No-JS:_ links navigate and submit buttons post.
- _Users:_ every screen.

**`ButtonGroup`**: merged into `Layout pattern="cluster"` with an `actions` alignment. No separate component.

**`CopyButton`**

- _API:_ `value`, `label`, `copiedLabel`, `size`.
- _Behavior:_ copies, then for 2 s shows a check and announces `copiedLabel` through a polite live region. Without JS it is hidden: wrap it with a selectable `Code` or `CopyField`.
- _Users:_ P5 (link, server address), P16, A3 (merge code), A4 (secret), L1, L2, X2.

**`Menu`**

- _Purpose:_ dropdown of actions or links (bits-ui DropdownMenu).
- _API:_ `trigger` (Button props), `items` of `{label, href?, onSelect?, icon?, danger?, disabled?, separator?}`, or `children` for custom groups (checkbox items for column visibility).
- _A11y:_ menu button pattern. Arrow keys, typeahead, Escape and Tab close, focus returns to the trigger.
- _No-JS:_ `nojs="details"` renders a `<details>` with a list of links or forms. Only link and form items are allowed in that mode.
- _Users:_ header user menu, LanguageSelector, P5 (more actions), DataTable column visibility, list row overflow actions in A4, S1 and M1.

**`Popover`**

- _Purpose:_ non-modal floating content (bits-ui).
- _API:_ `trigger`, `children`, `label`, `placement`.
- _Users:_ the chart legend on mobile, filter help, P8 (status explanation), the `Tooltip` fallback on touch devices.

**`Tooltip`**

- _Purpose:_ supplementary label only, never the sole source of information (bits-ui). Required on icon-only buttons.
- _API:_ `content`, `children`.
- _Users:_ icon buttons, chart marks (through the chart's own tooltip), truncated table cells.

#### Overlays

**`Dialog`** (rewritten)

- _API:_ `open` (bindable), `title`, `description?`, `closeLabel`, `size` (`sm | md | lg | full-mobile`), `trigger?` (Button props plus a label; omit it for a controlled dialog), `children`, `footer?` snippet, `dismissible` (default true; false while pending), `initialFocus?` (a selector or "first-field").
- _A11y:_ bits-ui modal, focus trap, returns focus to the trigger or a supplied `returnFocus` element, `aria-labelledby` and `aria-describedby`.
- _Responsive:_ full screen at the bottom of the viewport on small screens when `size="full-mobile"`.
- _No-JS:_ every dialog's trigger must have an `href` fallback to a page or route that performs the same task. `Dialog` takes `fallbackHref` and renders the trigger as a link until hydrated.
- _Users:_ the `ConfirmDialog` pattern, `Gallery` lightbox, P5 (render or video request), A2 (avatar picker), M3 (match picker).

**`Sheet`**

- _Purpose:_ a Dialog variant attached to the screen edge (`side` of `start | end | bottom`), for mobile filters and navigation.
- _Users:_ `SplitLayout` collapse, the header mobile navigation.

#### Feedback and status

**`Badge`**

- _API:_ `tone` (`neutral | info | success | warning | danger | accent`), `dot?`, `size` (`sm | md`).
- _Rule:_ state only: something that changes and the reader acts on, such as "pending", "approved", "outdated" or an unread count. At most one per item. Never color-only; the text carries the meaning.
- _Not for:_ categories, kinds, versions, version scopes or any other classification. Tags people can follow are `Chip`s; see `MetaList` for where the rest goes.
- _Users:_ build and draft status, record status, unread count, review state in table cells.

**`Chip`**

- _Purpose:_ an interactive tag. Badge is the static one.
- _API:_ `as` (link, button or static); `href?`; `pressed?` (toggle chip: `aria-pressed`); `removable?` with `removeLabel` (renders a separate remove button or link, `removeHref` for no-JS); `tone`; `icon`.
- _Users:_ P5 tags, P12, active filter chips in P2/P4/P7, schematic suggestions in S2, `ListField` tokens, P11.

**`Notice`**

- _API:_ `tone` (`info | success | warning | danger`), `title?`, `children`, `actions?` snippet, `role?` (`status | alert`; omit for static notices), `icon` (on by default, chosen by tone), `variant` (`inline | banner`; a banner spans full width above the page header).
- _Rules:_
  - Only one live notice per region.
  - `alert` is for failures the user must act on now.
  - `status` is for outcomes: saved, sent, approved.
  - It is the only success-feedback mechanism (no toasts).
- _Users:_ every mutation and status banner: pending build (P5), expiry (S2), consent (A1), DMs suspended (A7), flash messages after redirects.

**`SaveStatus`**

- _API:_ `state` (`idle | dirty | saving | saved | offline | error | conflict`), `labels` (one per state), `savedAt?` (a `Timestamp`), `onRetry?`.
- _A11y:_ `role=status`, announces only transitions to saved, error and conflict, throttled to at most one announcement every 5 s.
- _Users:_ S2, M4, A2, X1, `SettingsList`.

**`Progress`** (merged with Meter)

- _API:_ `label`, `value?` (omitted means indeterminate), `max`, `kind` (`progress | meter`), `valueLabel?` (e.g. "3 of 5 votes"), `thresholds?` (`[{value, label, tone}]`, drawn as markers), `tone`, `size`.
- _Renders:_ `<progress>` for progress. For meters, a `div role="meter"` with `aria-valuenow` and `aria-valuetext`, because the native `<meter>` styling is unreliable.
- _Users:_ uploads (S2), finalization (S3), vote tallies with pass and fail thresholds (M1, M2, A5), media limits (S2), draft completeness (S1, S2).

**`Spinner`**

- _API:_ `label`, `size`.
- _Rule:_ used only inside `Button` pending and inline beside under one line of text. Never for page or region loading.
- _Users:_ Button, Autocomplete, Combobox loading, CopyButton.

**`Skeleton`**

- _API:_ `shape` (`text | heading | block | avatar | card | row`), `lines?`, `width` (`full | 3/4 | 1/2 | 1/3`). It is `aria-hidden`. The parent region carries `aria-busy="true"` and a hidden loading label.
- _Users:_ island-loaded regions: P5 schematics, A6 inbox refresh, header counts, Combobox results, DataTable `loading` state, `ResultsView`.

**`EmptyState`** (pattern, but listed here because it is so common)

- _API:_ `title`, `description?`, `icon?`, `actions?` snippet, `size` (`inline | page`).
- _Users:_ every list: P2, P4, P7, P11, P12, P13, A3–A7, S1, M1, X2–X4.

**`ErrorState`**

- _API:_ `title`, `description`, `reference?` (error reference with copy), `onRetry?` or `retryHref?`, `retryLabel`.
- _Users:_ failures in island queries, P9 analysis failure, S3 dead jobs, and system pages.

#### Data display

**`Card`**

- _API:_ `as` (article, li, div), `href?` and `linkLabel?` (a stretched link: the heading's link covers the card while inner links stay clickable), `padding` (`sm | md | lg`), `tone` (`default | muted | selected | warning`), `media?` snippet (top, aspect ratio set by `mediaAspect`: `wide | square | video`), `header?`, `children`, `footer?`.
- _A11y:_ one link per card, on the heading. The stretched link uses a pseudo-element, so screen readers hear only the heading.
- _Users:_ BuildCard, RecordCard, Creator tiles, S1 drafts, P1 sections, P16, `ChoiceGroup` card variant, A4 installations.

**`Surface`**

- _Purpose:_ the old `panelClass`: a bordered surface with padding, no link.
- _Decision:_ merged into `Card` without `href`, and into `Section` with `surface` (pattern). No separate component.

**`DescriptionList`**

- _API:_ `items` of `{term, value?, numeric?, href?}` or `children` (`DescriptionItem` subcomponent with a value snippet); `layout` (`stacked | inline | grid`, where grid gives 2–4 columns responsively); `size`; `divided?`.
- _Renders:_ `<dl>`, with each pair in a `<div>`.
- _Users:_ P5 specs, P8 metrics, P9 point, P13 stats, A3, A4 device details, L1, L2, X2 detail, schematic metrics, M3.

**`StatGroup`**

- _API:_ `items` of `{label, value, unit?, href?, hint?}`, `size` (`md | lg`).
- _Renders:_ a `<dl>` with a large value.
- _Users:_ P1 counts, P5 key stats (size, opening time), P8 record metric, P13 stats, X2 summary.

**`List` and `ListItem`**

- _Purpose:_ a divided list of rows.
- _ListItem API:_ `leading?` snippet (icon, avatar, thumbnail), `title`, `href?` (stretched link), `meta?` snippet (badges, timestamp), `description?`, `trailing?` snippet (actions), `unread?` (accent marker plus hidden "unread" text from `unreadLabel`), `selected?`.
- _List API:_ `as` (ul or ol), `divided`, `density`, `label`.
- _Users:_ A6 inbox, A3 identities, A4 devices, S1 drafts, A7 subscriptions, P11 glossary, X3, X4, P8 holder history (compact), S2 uploaded files.

**`Timeline`**

- _API:_ `items` of `{id, datetime, dateLabel, title, href?, tone?, icon?, body?}`, with a `children` snippet per item when the caller is Svelte; `order` (`newest | oldest`).
- _Renders:_ `<ol>` with an accessible date per item.
- _Users:_ P6 build history, P8 record holder succession, S3 attempts, X2 log tail (optional).

**`Diff`**

- _API:_ `rows` of `{label, before, after, kind: added | removed | changed | unchanged}`, `labels` (`{before, after, empty}`), `showUnchanged?`, `layout` (`table | stacked`, stacked on mobile).
- _A11y:_ a table with column headers. Change kind is spelled out in text, not shown only by color.
- _Users:_ P6, M3, M4 (conflict review), A3 merge preview (counts), X5.

**`Image`**

- _API:_ `src`, `alt`, `width`, `height` (required: no CLS), `sizes?`, `srcset?`, `aspect` (`wide | square | video | natural`), `fit`, `loading` (`lazy | eager`), `fallbackLabel`, `fallbackIcon?`.
- _Behavior:_ the HTTPS check is a prop-level contract that features enforce. On error it swaps to the fallback; no inline `onerror` attribute (a hydrated `onerror` plus a CSS `:has` fallback without JS).
- _Users:_ BuildCard, Gallery, Avatar, P5, P16.

**`Avatar`**

- _API:_ `src?`, `name` (initials fallback, hidden from screen readers), `alt` (empty when decorative), `size`.
- _Users:_ P13, header user menu, A2, credits, M1 submitter.

**`Gallery`**

- _API:_ `items` of `{kind: image | video | embed, src, thumb, width, height, alt, caption?}`, `labels` (`{next, previous, close, openItem, of}`).
- _Behavior:_ a main viewer with a thumbnail strip, and a lightbox Dialog for full size. Arrow keys inside the lightbox. Videos use native `<video controls>` with a poster.
- _No-JS:_ the main item is the first image and thumbnails link to the full files.
- _Users:_ P5, M2, S2 (draft media preview).

**`Disclosure`**

- _API:_ `summary`, `children`, `open?`, `variant` (`plain | card`). Built on native `<details>`.
- _Users:_ P5 expansion, P8 evidence, P3 sections, X2 traceback, FAQ on P14, the `SplitLayout` no-JS fallback.

**`Tabs`**

- _API:_ `items` of `{id, label, count?, href?, disabled?}`, `value` (bindable), `mode` (`link | panel`), `label`, `children` snippet per panel (panel mode).
  - `link` mode renders `<nav>` with `aria-current="page"`. It is for URL state such as P2 scopes and A5 statuses, and works without JS.
  - `panel` mode uses the bits-ui Tabs pattern (arrow keys, automatic activation) for in-page content that doesn't need a URL. Use it sparingly, e.g. P5 schematic vs render.
- _Responsive:_ scrolls horizontally with edge fade. The current tab is scrolled into view.
- _Users:_ P2, P7, A5, A6 (all or unread), M1, P13 (builds or records).

**`SegmentedControl`**

- _API:_ `items` of `{value, label, icon?, href?}`, `value`, `mode` (`link | radio`), `label`, `size`.
  - Link mode is for view toggles in the URL (`view=cards|table`, record siblings: criterion and scope).
  - Radio mode wraps a native radio group with button styling, for forms such as duration units and vote options.
- _Users:_ P2, P4, P7 view toggle; P8 siblings; P13; `UnitField`; VoteControl.

**`Pagination`** (moved from features)

- _API:_ `prevHref?`, `nextHref?`, `pages?` (`{current, count, hrefFor(n)}`, offset listings only; the feature passes precomputed hrefs as an array of `{n, href}` for islands), `range?` (`{from, to, total}` together with a `rangeLabel` string), `labels` (`{nav, previous, next, page}`), `pageSize?` (`{value, options:[{value, href}]}`), `compact?` (mobile: prev, "3 / 12", next).
- _Behavior:_ previous and next keep their position and become disabled spans at either end, per the bot conventions. The whole control is omitted when there is a single page.
- _A11y:_ `nav` with a label, `rel=prev/next`, `aria-current="page"`.
- _Users:_ P2, P4, P6, P7, P11, P12, P13, A5, A6, M1, X2.

**`Breadcrumbs`**

- _API:_ `items` of `{label, href?}`, `label`.
- _Renders:_ `nav > ol`, with `aria-current` on the last item. Collapses middle items on mobile.
- _Users:_ P5 (Builds > Doors > title), P6, P8, P9 point, P12, M2, M3, M4, X2 detail.

**`NavList`**

- _Purpose:_ vertical section navigation.
- _API:_ `items` of `{label, href, icon?, count?, current?}` grouped by `{heading?, items}`, `label`.
- _Users:_ account sidebar (A2–A7), admin sidebar (X1–X8), the S2 section index (with completeness markers), the P3 table of contents, P14, the workbench.

#### Charts (LayerChart 2.x, pinned exactly; imported only in `src/ui/chart/*`)

**Principles**

- Features never import `layerchart`. Lint bans it outside `src/ui`.
- Every chart renders as static SVG on the server with no JS, and always ships an equivalent data table in a `Disclosure`, "Show data". The table is the accessible representation.
- Hydrate as an island only for tooltips or brushing (`interactive` prop).
- Colors come from `chart-*` tokens through Tailwind classes. No palette inside LayerChart config.
- Marks with `href` render as SVG `<a>` elements, so selection works without JS.

**`Chart`** (the frame every chart variant uses)

- _API:_
  - `title` and `description` (required): they become the `<figure>` caption and the SVG `aria-labelledby` / `aria-describedby`.
  - `height` (`sm | md | lg`).
  - `table` (`{caption, columns:[{key, label, numeric?}], rows}`, derived by default from series).
  - `legend` (`auto | none`).
  - `labels` (`{showData, hideData}`).
  - `interactive?`, `status?` (`ready | loading | empty | error`, with matching text props).
- _Renders:_ `<figure>`; the SVG gets `role="img"`, or `role="group"` when it contains links. A `<details>` holds the table.

**`SeriesChart`** (line, step and bar over a continuous or band x axis)

- _API:_
  - `kind` (`line | step | bar | stacked-bar`).
  - `x` and `y` axes: `{type: linear | integer | time | band, label, format?: (v) => string, domain?, nice?}`. Format functions only work from Svelte features; for Astro use, `tickLabels` is a precomputed array.
  - `series`: `[{id, label, tone: 1..6 | accent | muted, points:[{x, y, label?, href?}]}]`.
  - `annotations?`: `[{axis, value, label, tone}]` for threshold lines.
  - `tooltip?` (snippet, interactive only).
- _Keyboard (interactive):_ the plot is focusable, arrow keys move between points, the focused point's tooltip text is mirrored to a live region, and Enter follows its `href`.

**`RangeChart`** (intervals along an integer domain, for door range regions)

- _API:_
  - `segments`: `[{from, to | null (unbounded), step?, residue?, value?, label, tone, href?}]`.
  - `x` axis as above; `unboundedLabel`.
  - `selection` (bindable width), `onSelect`.
- Renders horizontal bands, with step and residue drawn as ticks. Supports a selected width.

**`DistributionBar`** (horizontal ranked bars, top N plus "other")

- _API:_ `items` of `{label, value, href?}`, `max`, `valueFormat`, `otherLabel`.
- Renders well without JS.

**Every chart the screens need**

| #   | Screen           | Chart                                                                               | Data shape                                                                                                                                                    | Interaction                                                                                                        | No-JS                                                        |
| --- | ---------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------ |
| 1   | P9               | Smallest door record by width (`RangeChart`, y = record volume or size per region)  | `DoorRangeResult.analysis.regions[]`: domain `{minimum, maximum \| null, period, residue}`, resolution, holder config ids → segments with holder build labels | Hover or focus shows holder and size. Click a width goes to `/at/[width]`. Selecting a width syncs with the table. | Static SVG with segment links, plus a table of the same data |
| 2   | P8               | Record progression (`SeriesChart` step)                                             | `[{date, metricValue, buildId, buildTitle}]` per criterion metric (needs API R3)                                                                              | Tooltip with build and date. Points link to the build.                                                             | Static plus table; the Timeline is the primary view          |
| 3   | P5               | Schematic block composition (`DistributionBar`)                                     | `SchematicMetrics.block_counts` → top 12 plus other                                                                                                           | None                                                                                                               | Full                                                         |
| 4   | M1, M2, A5       | Vote tallies                                                                        | `VoteTallies` against thresholds                                                                                                                              | A `Progress` meter, not a chart                                                                                    | Full                                                         |
| 5   | P13              | Creator activity: builds per year (`SeriesChart` bar, small)                        | `[{year, count}]` (API C1 stats)                                                                                                                              | None                                                                                                               | Full                                                         |
| 6   | P2, P4 (phase 2) | Range filter histogram: distribution of width or opening time for the current query | `distributions[field].histogram = [{from, to, count}]` (API S2)                                                                                               | Brush to set min and max fields (interactive). Keyboard users use the inputs.                                      | Inputs only; the histogram is decorative                     |
| 7   | X2               | Errors per day by surface (`SeriesChart` stacked bar)                               | `[{day, surface, count}]` (API AD2)                                                                                                                           | Tooltip. A bar links to the filtered table.                                                                        | Static plus table                                            |
| 8   | P1 (optional)    | Records broken per month (`SeriesChart` bar)                                        | `[{month, count}]`                                                                                                                                            | None                                                                                                               | Full                                                         |

### 2.2 Form primitives

**Shared field contract** (all fields)

- _Props:_ `label`, `detail?` (e.g. "optional", "ticks"), `hint?`, `error?`, `required?`, `disabled?`, `readonly?`, `name`, `id?`, `labelVisibility` (`visible | hidden`, hidden only inside tables or toolbars that have a visible heading).
- _Errors:_ rendered as text with an icon, linked through `aria-describedby`, and `aria-invalid` set. Never `role=alert`.
- _Width:_ `width` (`full | md | sm | xs`, tokens for 30, 16 and 8 characters) so numeric fields aren't full width.

**`FieldFrame`**: kept as the internal frame. Fix the alert role and the sizes.

**`TextField`** (renamed from `Field`)

- _API:_ native input props, `prefix?` and `suffix?` (text or icon, e.g. unit "ticks"), `counter?` (shows `n/max` when `maxlength` is set, announced politely at 90%), `clearable?` with `clearLabel`.
- _Users:_ everywhere.

**`TextareaField`**: adds `counter` and `autosize`. _Users:_ A2 bio, S2 description, M4 change summary, X1.

**`NumberField`**: merged into `TextField` with `inputmode="numeric"`, `type="text"`, plus `min`, `max`, `step` validation in the feature. Native `type=number` is avoided because of its wheel and locale problems.

**`SelectField`**: native select. _API:_ `options` of `{value, label, disabled?}` or a `children` snippet, and `placeholder`. _Users:_ sort in toolbars, X1, small forms.

**`CheckField`** and **`Switch`**

- `CheckField`: a single checkbox with a description.
- `Switch`: a native checkbox with `role="switch"`, for settings that apply immediately, with an optional `pending` state.
- _Users:_ A2 hidden profile, A3 identity public, A7 channels, X1 booleans, DataTable column menu, S2 boolean controls.

**`ChoiceGroup`** (merges radio group, checkbox group, and `FieldGroup` with options)

- _API:_ `type` (`radio | checkbox`), `options` of `{value, label, description?, icon?, disabled?}`, `value` (bindable), `variant` (`list | inline | cards`), `columns?`.
- _Renders:_ `fieldset` and `legend`. Arrow keys work natively for radios.
- _Users:_ S1 category cards, S2 choice and multi_choice controls (up to about 7 options), P2 filter enums, A7, VoteControl, X1.

**`FieldGroup`**: kept as a fieldset wrapper for related heterogeneous fields, such as door dimensions (width, height, depth) or a range. _Users:_ S2, P9 form, M4.

**`RangeField`**

- _API:_ `label`, `min` and `max` sub-fields (`{name, value, label}`), `unit?`, `bounds?`, `step?`. Two `TextField`s inside a `FieldGroup` with "to" between them, which reflows to stacked on mobile.
- _Users:_ P2, P4, P7 filters; P9 minimum and maximum width; X2 date range.

**`UnitField`** (duration and measurement)

- _API:_ `value` (the canonical number, e.g. game ticks), `units` (`[{id, label, factor}]`), `unit` (bindable), `labels`. Shows the converted value as a hint, e.g. "= 1.25 s".
- _Users:_ S2 duration control (game ticks, redstone ticks, seconds), M4, P2 timing filters.

**`Autocomplete`** (free text with suggestions, refit)

- _API:_
  - `load(value, cursor, signal)`, with suggestions shaped `{value, label, description?}` and an optional `replacement` span.
  - `minQueryLength`, `debounceMs` (250), `multiline?`, plus the field contract.
  - `labels` (`{suggestions, loading, error, noResults}`).
- _A11y:_
  - Combobox (ARIA 1.2), `aria-activedescendant`, option descriptions through `aria-describedby`.
  - Escape closes. Enter selects only while an option is active, otherwise it submits the form.
  - Input method editor (IME) composition is respected.
  - Result count announced politely ("5 suggestions").
- _No-JS:_ a plain input.
- _Users:_ the `SearchBox` query, S2 free-text fields with `option_source` hints, credit names, P13 alias claim.

**`Combobox`** (choose values from a set; bits-ui Combobox)

- _API:_ `options` or `load` (async with signal), `value` (string, or string[] when `multiple`), `multiple?` (selected values as removable `Chip`s inside the control), `allowCreate?` with `createLabel(text)` passed as a pre-bound string template, `labels`, plus the field contract.
- _No-JS:_ when `options` is static, render a native `<select>` (with `multiple`). When the options are async, fall back to a text input.
- _Users:_ S2 choice fields with more than 7 options or an `option_source`; the version picker (S2, P2, P9); tag pickers (P9, P2 filters, A7 filter subscription); M3 build match; X1 channel and role pickers; X6 subject picker.

**`ListField`** (repeatable rows)

- _API:_ `items` (bindable array), `row` snippet (`(item, index, rowLabel) => ...`), `newItem()`, `min`, `max`, `labels` (`{add, remove, moveUp, moveDown, rowLabel(n)}`), `reorderable?`.
- _A11y:_ each row is a `fieldset` with a legend, such as "Link 2". After add, focus moves to the new row's first field. After remove, focus moves to the next row, or to the add button. Removal is announced politely.
- _No-JS:_ not supported. It is used only on JS-required screens, or the server renders N blank rows.
- _Users:_ A2 profile links, S2 credits and showcase tags, S2 repeatable fields, M4.

**`FileDrop`** (replaces `FilePicker`)

- _API:_ `label`, `accept`, `multiple`, `maxBytes?`, `hint`, `error`, `disabled`, `onFiles`, `labels` (`{drop, browse}`). A native file input inside a drop zone, and the same file can be chosen again.
- _A11y:_ the input is the focusable element. Drag and drop is an enhancement only.
- _Users:_ the `MediaUploader` pattern (S2 media, schematics, supplied files).

**`CodeEntry`** (one-time code input)

- _API:_ `length`, `groups` (e.g. `[4, 4]`), `value` (bindable), `charset` (`alnum | digits`), `label`, `hint`, `error`, `autoSubmit?`.
- _Behavior:_ rendered as one input with visual grouping, not one box per character: paste works and screen readers read one field. Uppercases and strips separators. `autocomplete="one-time-code"`.
- _Users:_ L1, L2, A3 merge code.

**`ErrorSummary`**

- _API:_ `title`, `errors` of `{fieldId, message}`, `focusOnMount` (default true).
- _Behavior:_ `role=alert` with `tabindex=-1`. Receives focus after a failed submit. Each item links to `#fieldId`, and clicking focuses the field.
- _Users:_ S2 submit, M4, A2, X1, P9 form, A1 consent.

**`FormActions`**

- _API:_ `primary`, `secondary?`, `destructive?` (placed at the start, separated), `note?` (text above the row, such as "Publishing is final"), `sticky?` (sticks to the bottom of the viewport on long forms).
- _Users:_ S2, M4, A2, X1, L1, L2, dialogs.

### 2.3 Patterns (domain-free compositions)

**`PageHeader`** (replaces `PageHeading`)

- _API:_ `title`, `eyebrow?`, `description?`, `breadcrumbs?`, `meta?` snippet (badges and timestamps), `actions?` snippet, `media?` snippet (avatar), `notice?` snippet (banner `Notice`).
- _Behavior:_ the h1 gets `tabindex=-1` and is the focus target after client-side actions that change the whole page.
- _Users:_ every screen.

**`RelatedLinks`** (smart buttons: counts of related records, in a fixed place)

- _API:_ `items` of `{label, count, href, icon?}` and a `label` for the navigation landmark. `label` is the plural-aware text from the catalog, so the caller formats the count.
- _Renders:_ a `nav` with one bordered strip of links under the `PageHeader`, each a label with its count in a chip. It is a row when the container is wide and a full-width list when narrow, never a mix; dividers are 1px gaps so they survive wrapping.
- _Behavior:_ order and presence are fixed per screen, never by data. A zero count still renders ("0 records") so the row never reshuffles; an item is left out only on pages where it can never apply. Plain links, so no JS and no extra keyboard handling.
- _Labels:_ one or two words ("versions", "past holders"). The page header already names the subject, so "records held" not "records this build holds". Go longer only when two words would be ambiguous, and say why in `screens.md`.
- _Users:_ P5, P8, P12, P13.

**`Section`** (replaces `Panel`)

- _API:_ `title`, `level` (default 2), `description?`, `actions?` snippet (e.g. "View all"), `surface?`, `children`.
- _Renders:_ `<section aria-labelledby>`.
- _Users:_ every detail page.

**`NavBar`** (site header body, domain-free)

- _API:_ `brand` snippet, `links` of `{label, href, current}`, `actions` snippet, `labels` (`{primary, menu, close}`).
- _Behavior:_ on narrow screens the links collapse into a `Sheet` opened by a menu button. Without JS this is `<details>`.
- _Users:_ `CatalogueLayout`, `WorkbenchLayout`.

**`SearchBox`**

- _API:_ `action` (form GET URL), `name` (`q`), `value`, `label`, `placeholder`, `submitLabel`, `load?` (wires the `Autocomplete`), `hidden?` (extra hidden params to preserve, such as scope and view), `size` (`md | lg`).
- _No-JS:_ a GET form.
- _Users:_ P1, P2, P4, P7, P11, P15, header (compact).

**`FilterPanel`**

- _API:_ `action`, `children` (fields), `applyLabel`, `resetHref`, `resetLabel`, `activeCount`, `hidden?` (params to preserve), `autoApply?`.
- _Behavior:_ the form submits via GET. With JS it auto-applies on change, debounced, by navigating (`location.assign` with the new URL). Placed in `SplitLayout` with sheet collapse.
- _Users:_ P2, P4, P7, P12, P13 builds, M1, X2.

**`ActiveFilters`**

- _API:_ `items` of `{label, removeHref}`, `clearHref`, `labels`. A row of removable `Chip`s.
- _Users:_ P2, P4, P7, P12, X2.

**`ResultsToolbar`**

- _API:_ `count?` (a pre-formatted string such as "312 builds"), `sort?` (`{label, value, options:[{value, label, href}]}`, rendered as a `Menu` of links, falling back to `<details>`), `view?` (SegmentedControl link items), `columns?` (DataTable column visibility props), `extra?` snippet (e.g. a subscribe button).
- _A11y:_ the count text is rendered inside `role=status` only when results change client-side.
- _Users:_ P2, P4, P7, P12, P13, A5, M1, X2.

**`DataTable`** (`@tanstack/svelte-table` v9, which supports Svelte 5 directly)

- _API:_
  - `caption` (required; can be visually hidden).
  - `columns`: `[{id, header, accessor | cell snippet, align: start | end, numeric?, sortKey?, pinned?: "start", hideable?, defaultHidden?, width: xs | sm | md | lg | auto, truncate?}]`.
  - `rows`, `getRowId`.
  - `sort` (`{key, direction} | null`) and `sortHref(key, direction)`: the server sorts and the headers are links.
  - `visibility` (`{hidden: string[]}`) and `visibilityHref(hidden)`, from the URL `hide=`.
  - `expandable?` (`{getSubRows(row) | detail snippet, isOpen(row), toggleHref(row)}`), from the URL `open=`.
  - `density` (`comfortable | compact`), `loading?` (with skeleton row count), `empty` snippet, `footer` snippet (pagination).
  - `labels` (`{sortAscending, sortDescending, sortNone, expand, collapse, columns, scrollRegion}`).
  - `rowHref?` (stretched link on the first cell; never a clickable `<tr>`).
- _A11y:_
  - Semantic `<table>` and `<caption>`. `th scope=col`. The first pinned cell is `th scope=row`.
  - `aria-sort` on the sorted header. Sort links get the full name, e.g. "Width, sorted ascending, activate to sort descending".
  - Expand toggles are links or buttons with `aria-expanded` and `aria-controls`. Sub-rows get `aria-level` via data attributes, with indentation and a hidden "grouped under X" text.
  - The scroll container is `role=region`, `tabindex=0`, `aria-labelledby` the caption.
- _Responsive:_ horizontal scroll, a pinned first column (sticky, `z-sticky`, `bg-surface`, a shadow appears when scrolled), and a sticky header within the page scroll.
- _No-JS:_ fully server-rendered. Sort, expand and visibility are all links or a GET form. JS intercepts them, updates via `history.replaceState`, and refetches through Query, or navigates when the page isn't an island.
- _Users:_ P2 records, P2/P4 builds table, P7, P9 points, P12, P13, A5, M1, X2, X3, X4, X6, X8.

**`ResultsView`**

- _Purpose:_ chooses between the card grid and `DataTable` according to `view`, and owns the loading, empty and error states.
- _API:_ `view`, `cards` snippet, `table` snippet, `status`, `empty` and `error` snippets.
- _Users:_ P2, P4, P7, P12, P13.

**`ConfirmDialog`**

- _API:_ `title`, `description`, `consequences?` (list of strings), `confirmLabel`, `cancelLabel`, `tone` (`danger | default`), `requireText?` (`{label, expected}`, typed confirmation), `pending`, `error?`, `onConfirm` (async), `trigger` (Button props) or `open`, and `fallbackHref` (the no-JS confirm page).
- _Behavior:_ focus starts on Cancel for danger. While pending it cannot be dismissed. An error renders inside the dialog. On success the dialog closes and focus moves to a target the caller passes (`afterConfirmFocus`).
- _Users:_ A3 unlink and merge, A4 revoke and rotate, S1 and S2 discard draft, X2 clear, X5 clear profile, X6 revoke grant, M2 reject.

**`SettingsList`**

- _API:_ `items` of `{id, label, description?, control snippet, state: SaveStatus state, error?}`, `title`.
- _Behavior:_ each row saves immediately with its own status. No undo, per the bot convention: changing it back reverses it.
- _Users:_ A7, A3 visibility, A2 hidden, X1 (simple toggles), and later the deferred guild settings.

**`MediaUploader`**

- _API:_ `items` of `{id, name, kind, status: queued | uploading | processing | ready | failed | removed, progress?, thumb?, error?, primary?}`, plus `FileDrop` props, `limits` (`{count, max, bytesLabel}`), `onRetry`, `onRemove`, `onUndoRemove`, `onSetPrimary?`, `labels`.
- _Behavior:_ a list of `ListItem`s with `Progress`. Removal shows an inline "Removed · Undo" row instead of a confirm, since the reference is only marked discarded.
- _A11y:_ per-item status changes are announced politely and aggregated ("2 files uploaded, 1 failed").
- _Users:_ S2 media, S2 schematics, S2 supplied files, A2 avatar (if uploads are added).

**`OneTimeSecret`**

- _API:_ `value`, `label`, `warning` (must be saved now), `copyLabel`, `acknowledgeLabel`, `onAcknowledge`.
- _Behavior:_ the value is in a read-only `Code` with `CopyButton`. Navigating away before acknowledging triggers `beforeunload`.
- _Users:_ A4 installation create and rotate, A3 merge code (expiry shown), CLI token display if it is ever shown on the web.

**`StepFlow`**

- _API:_ `steps` of `{id, label, status: pending | current | done | error}`, `current`, and a `children` snippet for the current step's content. Renders `<ol>` with `aria-current="step"`.
- _Users:_ L1, L2 (enter code, review, approved), A3 merge (code, preview, confirm), S3 finalization status.

**`Announcer`**

- _Purpose:_ one page-level polite live region (a singleton module) for island messages that have no natural home: result counts after client-side refetch, "Marked all read". Lives in the layout.
- _Users:_ P2, A6, DataTable.

**`FlashNotice`**

- _Purpose:_ reads the one-shot flash the server set after a POST-redirect-GET and renders a `Notice role=status` in the `PageHeader` slot. The cookie is owned by `src/server/cookies.ts`; the layout passes text.
- _Users:_ M4 after save, A2, A4, X1, S1 after discard.

### Merged or dropped

- `LinkButton` becomes `Button href`.
- `Panel` becomes `Section`; `SummaryCard` becomes `Card`.
- `PageHeading` becomes `PageHeader`.
- `FilePicker` becomes `FileDrop`.
- `Field` is renamed `TextField`.
- `Meter` merges into `Progress`.
- `Kbd` merges into `Code`.
- Toast: never.
- `IconButton` becomes `Button iconOnly`.
- `Surface` becomes `Card` or `Section surface`.

### 2.4 Tokens to add or change (`app.css` `@theme`, `themes.css`)

**Color roles** (light and dark, `oklch`)

- `line-strong`: control borders, at least 3:1 against `surface`.
- `info`, `info-soft`, `danger-soft`.
- `on-danger`: text on a filled danger button.
- `surface-raised`: menus, popovers and dialogs in dark mode, where `surface-muted` currently doubles as hover.
- `highlight` and `on-highlight`: search match `<mark>`.
- `chart-1` … `chart-6`: categorical, validated for distinguishability in both themes and for at least 3:1 against `surface`. `chart-grid` and `chart-axis`.
- `skeleton`, `skeleton-shine` (with reduced motion: no shine).
- `selection`.
- `scrim-edge`: the scroll shadow on a pinned column.

**Type scale** (`--text-*` with paired line heights)

- `xs` 0.8125rem (13 px) / 1.5. This overrides Tailwind's 12 px for CJK legibility. Badges, hints and errors use it, replacing 0.6875 and 0.8125.
- `sm` 0.875 / 1.55, `base` 1 / 1.6, `lg` 1.125 / 1.5, `xl` 1.25 / 1.4, `2xl` 1.5 / 1.3, `3xl` 1.875 / 1.2.
- `display`: `clamp(2rem, 5vw, 3.5rem)` / 1.1, home hero only. Page titles use `3xl`.

**Weights**: 400, 600, 700 only. Remove 650 and 750.

**Families**: one sans face for all interface text, numbers and measurements included; use tabular figures (`numeric`) where digits must line up. Monospace is only for text a reader copies or types exactly: code, commands, keys, tokens and error references, all through `Code`.

**Tracking**: `--tracking-tight` (-0.02em) and `--tracking-display` (-0.04em), both reset to 0 under `:lang(zh)`.

**Spacing and sizes**

- `--spacing-gutter`: `clamp(1rem, 4vw, 3rem)`.
- `--spacing-control`: 2.75rem (44 px); `--spacing-control-sm`: 2.25rem.
- `--spacing-header`: header height, used for scroll-margin on anchors.
- Containers: `--container-content` 76rem (keep), `--container-prose` 42rem, `--container-form` 48rem, `--container-wide` 96rem (workbench and admin tables).
- Dialogs: `--container-dialog-sm` 26rem, `--container-dialog-md` 32rem, `--container-dialog-lg` 48rem.
- Grid minimums: `--container-tile` 12rem, `--container-card` 18rem, `--container-card-wide` 22rem. These replace the two arbitrary `minmax` values in the features.
- Field widths: `--container-field-xs` 8ch, `--container-field-sm` 16ch, `--container-field-md` 30ch.

**Radii**: `--radius-xs` 0.25rem (menu items, chips), `sm` 0.375rem (controls), `md` 0.75rem (cards and panels), `lg` 1.25rem (dialogs and hero), `full`.

**Shadows**: `card`, `pressed` (keep), `overlay` (menus and dialogs), `sticky` (pinned column and header edge).

**Motion**: `--duration-fast` 120ms, `--duration-normal` 200ms, `--duration-slow` 320ms, `--ease-standard`, `--ease-emphasized`. Keep the reduced-motion reset.

**z-index**: defined as `@utility` classes, not numbers: `z-sticky` (10), `z-header` (20), `z-dropdown` (30), `z-overlay` (40), `z-modal` (50), `z-skiplink` (60).

**Breakpoints**: keep Tailwind's defaults and stop using `max-[65rem]` and `max-[70rem]`. Use `md` and `lg` only.

**Theme meta**: `themes.ts` gains a `themeColor` for each theme, consumed by `DocumentHead`.

---

## 3. Feature-level components

The primitives each one composes are listed after the arrow.

**Shell (`src/layouts`, Astro)**

- `CatalogueLayout`: SkipLink, NavBar, Container, Announcer, FlashNotice.
- `SiteHeader`: NavBar, SearchBox (compact), `UserMenu` island (Avatar, Menu, Badge unread count), Button for sign in.
- `SiteFooter`: Layout, Link.
- `LanguageSelector`: Menu in `nojs="details"` link mode.
- `DocumentHead`: unchanged, plus a theme color derived from the token.

**`features/builds`**

- `BuildCard`: Card, Image, DescriptionList, Text.
- `buildColumns`: DataTable column specs.
- `BuildHeader`: PageHeader, Badge, Breadcrumbs, Button, Menu.
- `BuildStats`: StatGroup, DescriptionList.
- `BuildMedia`: Gallery.
- `CreditList`: List, Avatar, Link.
- `VersionSupport`: Text.
- `BuildTags`: Chip, grouped by kind.
- `RecordsHeld`: List.
- `SchematicPanel`: Section, DescriptionList, Button, DistributionBar, Disclosure.
- `RenderRequest` and `VideoRequest`: Dialog, ChoiceGroup, Progress, Notice.
- `SponsorCard`: Card, CopyButton.
- `DoorExpansion`: Disclosure, DescriptionList, Code.
- `BuildHistory`: Timeline, Diff, Pagination.
- `BuildStatusBanner`: Notice.
- `BuildStatusBadge`: Badge.

**`features/records`**

- `RecordCard` (replaces `RecordSummary` and `SummaryCard`): Card, Text, MetaList, Badge.
- `recordColumns`: DataTable column specs.
- `RecordHeader`: PageHeader, Badge, SegmentedControl for siblings.
- `RecordFreshness`: Text, Timestamp.
- `RecordStatusExplainer`: Popover, Notice.
- `HolderList`: Layout grid, BuildCard, StatGroup.
- `RecordProgression`: SeriesChart, Timeline.
- `DoorEvidence`: Disclosure, DescriptionList.
- `DoorRangeForm`: FilterPanel, ChoiceGroup, Combobox, RangeField, TextField.
- `DoorRangeExplorer`: RangeChart, DataTable, ErrorState.
- `DoorRangePoint`: DescriptionList, List.
- `RecordCheckForm` and `RecordCheckResult`: FieldGroup, UnitField, Combobox, Notice, HolderList.

**`features/search`**

- `SearchPage`: SearchBox, Tabs, ActiveFilters, FilterPanel, ResultsToolbar, ResultsView, Pagination.
- `queryAutocomplete`: the Autocomplete loader against `/v1/search/suggest` and `/v1/suggest`.
- `SearchResultCard`, a discriminated union: BuildCard, RecordCard, or `MetadataResult` (Card).
- `RecordResultsTable`: DataTable with dynamic metric columns built from search fields, expandable when grouped by build.
- `FieldFilters`: generates RangeField, ChoiceGroup and Combobox from the field definitions and writes the query string.
- `MatchHighlight`: renders `SearchText` spans as `<mark>`.
- `SearchHelpReference`: Prose, DataTable, Chip example links.
- `SubscribeToQuery`: Button, ConfirmDialog.

**`features/tags`**

- `TagHeader`: PageHeader, DescriptionList.
- `TagGlossary`: SearchBox, List, Section per kind.
- `TagChip`: Chip.

**`features/creators`**

- `CreatorHeader`: PageHeader, Avatar, Link list, Badge.
- `CreatorStats`: StatGroup, SeriesChart.
- `AliasList`: Chip.
- `ClaimAlias`: Notice, Button, Dialog.
- `CreatorWork`: Tabs, ResultsView.

**`features/submissions`**

- `FormRenderer`: maps `ControlKind` and `ValueKind` to fields:
  - `text` to TextField or TextareaField;
  - `number` to TextField numeric;
  - `choice` to ChoiceGroup (7 or fewer static options) or Combobox;
  - `multi_choice` to ChoiceGroup checkbox or Combobox multiple;
  - `duration` to UnitField;
  - `boolean` to CheckField;
  - `credits` to CreditsField;
  - `showcase_tags` to ShowcaseTagsField.
    It evaluates `visible_when`, `repeatable` (via ListField), `required_capability` and `origins`, and is also used by M4.
- `CreditsField`: ListField, Combobox, SelectField for the role.
- `ShowcaseTagsField`: ListField, Combobox, TextField.
- `DraftAutosave`: the operation queue engine plus SaveStatus. Logic lives in `draft-sync.ts`.
- `DraftEditor`: SplitLayout, NavList (section index with completeness), Section, FormActions, ErrorSummary.
- `CategoryPicker`: ChoiceGroup cards.
- `DraftList` and `InferredDraftList`: List, Badge, Progress meter, Timestamp, Menu, ConfirmDialog.
- `DraftMedia`, `DraftSchematics` and `SuppliedFiles`: MediaUploader.
- `SchematicSuggestions`: Chip ("Use 5 wide"), Notice.
- `ManifestUpgradeNotice`: Notice with action.
- `SubmitPanel`: ErrorSummary, FormActions, Notice.
- `SubmissionStatus`: StepFlow, List of issues linking to fields, Button to the build.

**`features/account`**

- `ProfileForm`: TextField, TextareaField, ListField, Switch, SaveStatus, FormActions.
- `AvatarPicker`: Dialog, ChoiceGroup, Avatar.
- `IdentityList`: List, Avatar, Switch, ConfirmDialog.
- `MinecraftRefresh`: Button pending, Notice, DescriptionList.
- `MergeFlow`: StepFlow, OneTimeSecret, CodeEntry, Diff or DescriptionList, ConfirmDialog with `requireText`.
- `ConsentGate`: Prose, FormActions.
- `DeviceList` and `GrantList`: List, DescriptionList, ConfirmDialog.
- `InstallationList`, `InstallationForm` and `SponsorProfileForm`: List, Dialog, TextField, Switch, OneTimeSecret, ConfirmDialog.
- `MyBuilds`: Tabs, DataTable or List, Progress meter.
- `AccountNav`: NavList.

**`features/notifications`**

- `InboxList` and `InboxItem`: List, ListItem unread, Timestamp, Link. Per-kind message mapping is checked with `satisfies Record<NotificationKind, …>`.
- `NotificationBell`: Button iconOnly, Badge.
- `PreferencesForm`: SettingsList, Switch, Notice.
- `SubscriptionList`: List, ConfirmDialog.
- `FollowButton`: Button toggle with `aria-pressed`, optimistic. Used on P5, P8, P12 and P13.

**`features/device-link`**

- `DeviceApproval`, shared by CLI and Minecraft: StepFlow, CodeEntry, DescriptionList, Notice warning, FormActions.

**`features/review`**

- `ReviewQueue`: Tabs, DataTable, Progress meter, Badge.
- `VoteControl`: SegmentedControl radio, Button pending, Progress meter with thresholds, Notice.
- `VoteTally`: Progress, DescriptionList.
- `CorrectionReview`: Diff, Combobox, Notice, FormActions.
- `BuildEditForm`: FormRenderer, SaveStatus, Diff on conflict.

**`features/admin`**

- `RuntimeSettingsForm`: SettingsList, Section, Combobox, TextField, SaveStatus.
- `ErrorReportTable`: FilterPanel, DataTable, SeriesChart.
- `ErrorReportDetail`: DescriptionList, Code, Disclosure, CopyButton.
- `TagProposalQueue`, `AliasClaimQueue`: DataTable, ConfirmDialog.
- `ProfileModeration`: DescriptionList, Diff, ConfirmDialog.
- `PermissionExplorer`: NavList, DataTable, Combobox, Badge.
- `RecordOps`: DataTable, Button, Notice.
- `VersionList`: DataTable, Dialog.
- `AdminNav`: NavList.

**`features/home`**

- `HomeHero`: Heading display, SearchBox.
- `CatalogueCounts`: StatGroup.
- `RecentRecordChanges`: List.
- `NewBuilds`: Layout grid of BuildCard.

**`features/content`**

- `ContentPage`: Prose, NavList as table of contents.

**Shared helpers** (domain-free ones in `src/ui/lib/`)

- `url-state.ts`: `withParams`, `toggleListParam`.
- `focus.ts`: `focusHeading`, `focusAfterRemoval`.
- `query-client.ts`: one QueryClient per page, shared by all islands.
- `mutation.ts`: CSRF token cache, idempotency keys, If-Match, ProblemDetail parsing with zod. This one knows about the API, so it lives in the `src/api-client` layer, which features may import, not in `src/ui`.

---

## 5. Interaction patterns to standardize

### Forms and validation

- **Server-first where possible.** Simple forms (A2, A7 single fields, L1, L2, admin actions) are HTML forms that POST to an Astro endpoint. The endpoint calls the API with CSRF and redirects back with a flash notice (POST-redirect-GET). Islands enhance them with Query mutations.
- **When to validate:**
  - Format and range errors on blur.
  - Required fields only on submit.
  - Errors clear as soon as the value becomes valid, on input.
  - Server errors map to fields through `context.fields`, or for drafts through `issues` and `params`.
- **Failed submit:** render `ErrorSummary` at the top of the form, focus it, and link each item to its field. Field errors are plain text under the field.
- **Submit buttons stay enabled.** The web shows the summary instead of greying out Submit. Deliberately different from the bot convention, because on the web a disabled button explains nothing and isn't focusable. Exception: while pending.
- **Labels:** every field has a visible label. Optional fields are marked "(optional)" through `detail`; required fields are not marked.
- **Rows of fields:** fields side by side either all show their labels or all hide them (a toolbar under a visible heading). A button that acts on one field, such as Search, comes from the component that lines it up with the field's box (`SearchBox`); a form's submit goes in `FormActions` under the fields. The review suite's `toHaveAlignedControls` fails when controls in a row start at different heights, and the reflow sweep runs the same check at every width in the pseudo-locale.

### Draft autosave (S2, and M4 when it moves to drafts)

- **One queue per draft** (`draft-sync.ts` in `features/submissions`). The UI holds local values; edits produce operations:
  - `set` with the value;
  - `unset` when a field is cleared, an optional field is emptied, or a conditional field becomes hidden.
- **Flushing:** text fields after 800 ms idle or on blur. Choices, toggles and uploads immediately. A field's newer operation replaces its older unsent one.
- **Each batch** is one `DraftChangeRequest`:
  - `base_revision` is the last acknowledged revision;
  - `client_instance_id` is per tab, kept in `sessionStorage`;
  - `idempotency_key` is per batch and reused on retry.
  - Only one request is in flight; the queue waits for it.
- **On success:** take the returned revision and issues. Don't overwrite fields with server values if the user typed since sending; only apply server values for fields with no pending local operation.
- **On a revision conflict:** fetch the draft and rebase the pending operations onto it. If the remote side changed a field that also has a pending local operation, mark the field `conflict` with "Keep mine / Use theirs", and `SaveStatus` shows conflict.
- **Network errors and offline:** keep pending operations, mirrored to `sessionStorage`, so a reload restores them with a "Restored unsaved changes" notice. Retry with backoff (1, 2, 4, 8 s, capped at 30 s) and on the `online` event. `SaveStatus` shows offline or error with a retry.
- **Leaving the page:** a `beforeunload` guard only while operations are unsent.
- **Expiry:** at 24 h before `expires_at`, show a warning Notice. Expired drafts are read-only, with "Start a new draft from this".
- **Manifest upgrade:** when `schema_revision` is behind the current manifest, show a Notice offering an upgrade. Never upgrade automatically.

### Destructive actions

| Tier                    | Examples                                                                                                  | Pattern                                                                                                              |
| ----------------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Reversible              | Remove media or a schematic from a draft, unsubscribe, mark read                                          | No confirmation. Inline "Removed · Undo" where undo exists; otherwise the toggle is its own undo.                    |
| Irreversible, contained | Revoke device or grant, unlink identity, discard an empty draft, reject a proposal                        | `ConfirmDialog` naming the object ("Revoke _Laptop CLI_?"), with the danger button labelled with the verb, not "OK". |
| Irreversible, broad     | Merge accounts, rotate installation secret, clear all errors, clear profile, discard a draft with content | `ConfirmDialog` with a consequences list, plus typed confirmation for merge and clear-all.                           |

Without JS, the trigger links to a confirmation page (for example `/account/devices/[id]/revoke`) containing the same text and a POST form.

### Optimistic updates

- **Allowed** for idempotent, low-stakes toggles: follow or unfollow, mark read or unread, identity visibility, notification channel switches, vote cast and clear.
- **How:** a Query mutation with `onMutate` snapshot and rollback. On rollback, the inline error appears beside the control and focus stays put.
- **Never optimistic:** finalization, approvals and rejections, settings with thresholds, merges, anything destructive.

### Pagination

- **Cursor listings**: previous and next only, plus "x–y of total" when `total` is known.
- **Offset listings** (search): page numbers capped at the API maximum, and a page-size menu from 20 to 50.
- Pages after the first are `noindex`.
- After paging, the scroll position goes to the results heading (`PageHeader` or the `Section` heading). On full navigation, the `#results` anchor is added to the href.
- No infinite scroll anywhere. An exception may be considered for the A6 inbox "Load older" button, which is still a link.

### URL state

- **All listing state is in the query string:** `q`, `scope`, `sort` (`field` or `-field`), `view`, `offset` or `cursor`, filters, `hide` (hidden columns), `open` (expanded groups).
- **Default values are omitted.** The canonical URL drops paging and view.
- **Readers** are zod-parsed in features. An invalid value falls back to the default and never errors, as `readPageAddress` already does.
- **Island updates** use `history.replaceState` for view, columns and expansion (no new history entry) and `pushState` for query, filter, sort and page changes, so Back works.
- **Locale prefixes** are preserved via `localizePath`, and the language selector keeps the query.

### Focus management

- **Full navigations:** browser default, plus the skip link. After a POST-redirect-GET with a flash notice, the flash is rendered with `role=status` near the top and focus is not moved.
- **Island list refetch:** focus stays on the control that changed; the new count is announced through `Announcer`.
- **Removing an item:** focus the next item's primary control, else the previous one, else the list heading (`focusAfterRemoval`).
- **Adding an item:** focus its first field.
- **Dialogs:** return focus to the trigger, or to `afterConfirmFocus` when the trigger was removed.
- **Failed submit:** focus `ErrorSummary`.
- **Moving between steps** (StepFlow, S2 sections): focus the step heading, which has `tabindex=-1`.
- **Anchor targets** get `scroll-margin-top: var(--spacing-header)`.

### Loading

- **Server-rendered pages show no loading UI.** Data loads before the template renders, per AGENTS.md.
- **Island regions with a known shape** (lists, cards, tables, header counts) use `Skeleton`, shown only after a 200 ms delay. The region gets `aria-busy`, and stale data is kept visible during a refetch (`placeholderData: keepPreviousData`) with a subtle busy state instead of skeletons.
- **Spinners** only inside a pending `Button`, or inline beside a single line of text.
- **Measurable work** (uploads, render jobs) uses `Progress`.
- **Multi-phase jobs** (finalization, video rendering) use `StepFlow` with the current phase.

### Empty states

- **First use:** what the area is for, plus a primary call to action, e.g. no drafts → "Submit a build".
- **No results:** echo the query, offer to remove the last filter, link to clear all, switch scope, and link to search help.
- **Not available yet or disabled:** explain why, e.g. "Notifications are off · Turn on".
- **Never** an empty table with headers only.

### Errors

- **Page loads:** `load()` maps 404 and 503 (existing). Island query failures render `ErrorState` with retry and the error reference.
- **Mutations:** an inline `Notice role=alert` beside the action, or inside the dialog. The message is mapped from `ProblemDetail.code` through a `satisfies Record<ErrorCode, …>` map, with a generic fallback showing the reference and a copy button.
- **Revision conflicts:** a `Notice` offering "Reload" with a `Diff` of what changed.

### Contextual actions

- Detail pages put verbs (vote, edit, suggest a correction, claim) in the `PageHeader` `actions` slot. Related records are `RelatedLinks`, not actions.
- At most one primary `Button`, chosen by a priority fixed per screen in `screens.md`. Other actions go into an overflow `Menu`, in the same order every time.
- An action that applies but is unavailable stays visible with its reason ("Voting closed 2 days ago"). Actions are never suggested by heuristics: the same page state always shows the same actions.

### Permission gating and sign-in state

- **Three cases:**
  - Can never do it: hide the control.
  - Could do it after signing in: show it as a link to `/sign-in?redirect_to=<current URL>&intent=<action>`. The intent is replayed after login, e.g. auto-follow, after a confirmation.
  - Signed in but temporarily blocked (consent pending, DMs suspended): show it with an explanation and a fix.
- **Staff controls** are never rendered for non-staff, not even disabled. The server enforces every permission anyway; UI gating uses `session.permissions` and `viewer` blocks only.
- **Rendering and caching.** Public catalogue pages (P1–P16) render identical HTML for everyone, so they stay cacheable and fast. Viewer-specific parts (user menu, follow state, vote panel, edit button) are small islands fed by `/v1/session` and the `viewer` blocks through Query. Account, submission, review and admin pages render with the session on the server (`Cache-Control: private, no-store`) and redirect anonymous visitors to sign-in.
- **Trade-off:** without JS, public pages don't show personalized controls. Accepted.

---
