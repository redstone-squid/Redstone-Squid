# Discord command experience redesign

> **Status.** In progress. Phases land one at a time; each phase has its own plan file in this
> directory and is marked here when delivered.

## Why

Dogfooding findings from actually using the bot (2026-08-17). The command surface grew feature
by feature and it shows: ~108 commands across 19 top-level groups, several of which overlap,
fight the Discord UI, or demand more typing than the task deserves. Concrete complaints, from
the person actually using it (a fuller code-level inventory lives in
[00-audit.md](00-audit.md)):

- **Submission is split across two commands with opposite flaws.** `/build submit` asks for
  four attachments *first* and then hides every typed field behind a modal — and Discord modals
  have no autocomplete, so the fields with taxonomies behind them (patterns, restrictions,
  versions, creators) degrade to blind free text. `/build submit-full` has the autocomplete but
  dumps ~17 flag options on you at once, near Discord's 25-option cap. The result: the guided
  path is the worse path, which defeats its purpose.
- **Search is scattered and noisy.** `/search` plus `/restrictions search` plus
  `/patterns search` plus `/patterns list` are four entries into what is one question, and most
  of them are rarely useful. Result lists surface a raw relevance score that means nothing to a
  reader.
- **Diagnostics fight the reader.** `/error` (the `show` fallback) does not work as a prefix
  command in practice, and the traceback cannot be expanded past what one message shows.
- **Settings are one-at-a-time.** `/settings set` takes a single key per invocation; first-time
  setup of a guild is a dozen round trips. No panel view, no batch edit.
- **Too many commands overall.** Every subsystem grew its own group (`patterns`,
  `restrictions`, `version`, `tag`, `vote`/`poll`, `admin records-*`, …). Many are
  staff-or-never commands sitting in everyone's command picker.

The audit added the systemic causes behind those complaints, the biggest being: **no command
sets `default_permissions`, so all ~108 commands appear in every user's picker** and staff
gates only fire at runtime; ephemerality and i18n are applied inconsistently; five commands
take pasted message links where a right-click context menu belongs; raw IDs, UUIDs, and
ranking scores leak into user-facing output; and pagination is reinvented (or skipped) per
command. See [00-audit.md](00-audit.md) for the per-group breakdown.

## Principles

1. **Typed options are the entry point; interactive views are the workspace.** Slash options
   get autocomplete, modals do not — so anything backed by a suggestion source must be
   enterable as an option. Views/modals remain for what options cannot do: iteration, preview,
   confirmation, and long text.
2. **One verb, one command.** A task should have exactly one obvious entry point. Variants
   ("full", "quick", per-noun search clones) merge into the one command's options.
3. **Order options by importance, attachments last.** The tab order through options is the
   form; it should read like one.
4. **Show data, not mechanics.** Relevance scores, internal ids, and other machinery stay out
   of user-facing result lists unless they help the reader decide something.
5. **Every removal is a deliberate taxonomy edit.** `tests/unit/bot/test_command_taxonomy.py`
   pins the public surface; each phase updates it in the same commit as the change.

## Phases

| # | Plan | Scope | Status |
|---|------|-------|--------|
| 0 | [00-audit.md](00-audit.md) | Code-level audit of all 19 groups; cross-cutting defects C1–C7 | **Done** |
| 1 | [01-build-submit.md](01-build-submit.md) | One `/build submit`: typed fields with autocomplete first, attachments last, workspace kept; `submit-full` removed | **Delivered** |
| 2 | 02-search.md (todo) | Fold `/restrictions search`, `/patterns search`, `/patterns list` into `/search`; drop scores and raw ids from result lists; demote the `mode`/`sort`/`direction` enums | Not started |
| 3 | 03-diagnostics.md (todo) | `/error`: fix the prefix form (missing `invoke_without_command=True`), make `recent` entries openable in place | Not started |
| 4 | 04-settings.md (todo) | Settings panel: view everything at once, edit several keys per trip; bring `voting` replies onto i18n/layouts | Not started |
| 5 | 05-condensation.md (todo) | Merge, hide, or gate the long tail, with audit items C2–C7 as the checklist | Not started |

The audit also surfaced one item worth doing *before* phase 2: **C1, picker visibility** —
adding `default_permissions` to the staff groups so non-staff pickers shrink from ~108
commands to the handful they can run. It is a few lines per group and changes how much of
phase 5 is even necessary. Phase 5 stays last otherwise: merging the long tail is easier once
phases 1–4 have established the target shapes (typed-options-plus-workspace, single-entry
search, panel-style settings).
