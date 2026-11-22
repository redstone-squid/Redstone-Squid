# Catalogue design system

Open `/design-system` to exercise the components used by the catalogue. Astro renders
the initial preview from the URL. Svelte controls update the fixtures and query in
place so the current preview is shareable. Use `/zh-cn/design-system` for Simplified Chinese.

## Composing a screen

| Need                                                              | Use                                                                                                                                                           |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Page width, vertical rhythm, wrapping actions, responsive columns | Astro `Layout` or `layoutStyles` on the appropriate semantic HTML element in an island. Choose a pattern and, when needed, a compact, normal, or section gap. |
| Section with a heading and description                            | `Panel`. Use a native heading for a section that needs no surface.                                                                                            |
| Navigation or a command                                           | `LinkButton` for a URL, `Button` for an action, and the shared `buttonStyles` for equivalent native markup.                                                   |
| User input                                                        | `Field` for text and numbers, `SelectField`, `TextareaField`, `CheckField`, and `FieldGroup` for related choices. Each renders native controls.               |
| Suggestions and modal forms                                       | Svelte `Autocomplete` and `Dialog`. Feature code supplies options, cancellation-aware loaders, or form content.                                               |
| State and feedback                                                | `Badge` for short metadata, `Notice` for messages and recovery actions, `Progress` for an operation with measurable progress.                                 |
| Attachments                                                       | `FilePicker` handles file selection and reselection. The submission feature owns accepted formats, transport, progress, and retry.                            |
| Browsing data                                                     | Reuse the catalogue's build previews, record summaries, pagination, and language selector.                                                                    |

A field takes its visible label, hint, error, and native control props. The
component associates descriptions with the input and preserves caller-provided
description IDs. Put the error against the field the user can correct. Use a
notice for a failure that applies to the whole operation. Set its role according
to how the message should be announced. Static notices need no live region.

Keep API calls, draft state, validation rules, and translations with the feature.
For an ordinary screen, compose the existing pieces first. Keep CSS for a unique
arrangement beside the feature. When multiple callers need the same behavior,
promote it to a component and add a story using the production implementation.

## Styles and rendering

Components style themselves with Tailwind utilities from `app.css`, which exposes
only the theme colors defined per theme in `themes.css` and `themes.ts`. Merge a
caller's `class` with `cn`. For native markup without a component, use
`buttonStyles`, `layoutStyles`, `noticeStyles`, and the class constants in
`variants.ts` (`inputClass`, `fieldClass`, `panelClass`, `kickerClass`,
`mutedClass`). The layouts serve the fonts locally. Use sentence case for headings, labels, and badges. Reserve
monospace for technical values.

Astro owns server-rendered routes. Use Svelte for new interactive islands.

Shared UI takes text through props; callers pass messages from `@/locales/messages.gen`
(see `web/AGENTS.md`). Catalogue URLs use locale prefixes;
the workbench uses a language query parameter. The language selector preserves the URL query.

## Review

From `web/`, run `bun run check`, `bun run build`, `bun run check:bundle`, and
`bun run test:review`. Install the pinned browser with
`bunx playwright install chromium`. Browser checks cover keyboard navigation,
accessible names and descriptions, rendering without JavaScript, light and dark
themes, desktop and mobile. `/design-system-trial` exercises a complete draft.
Review the captured workflows in `review-gallery/index.html`.

For an intentional visual change, inspect the refreshed screenshots before
committing them and rerun the review against the saved baselines. Keep the OS,
fonts, and browser revision consistent. `bun run test:compatibility` runs the
draft trial in Firefox and WebKit. Install their
system libraries and pinned browsers first. Firefox skips the draft-form axe
scan because revision 1572 crashes during that scan.
