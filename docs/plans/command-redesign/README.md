# Discord command experience redesign

> **Status.** In progress. Phases land one at a time; each phase has its own plan file in this
> directory and is marked here when delivered.

## Why

Dogfooding findings from actually using the bot (2026-08-17). The command surface grew feature
by feature and it shows: ~90 commands across 19 top-level groups, several of which overlap,
fight the Discord UI, or demand more typing than the task deserves. Concrete complaints, from
the person actually using it:

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
| 1 | [01-build-submit.md](01-build-submit.md) | One `/build submit`: typed fields with autocomplete first, attachments last, workspace kept; `submit-full` removed | **Delivered** |
| 2 | 02-search.md (todo) | Fold `/restrictions search`, `/patterns search`, `/patterns list` into `/search` scopes or autocomplete; drop raw scores from result lists | Not started |
| 3 | 03-diagnostics.md (todo) | `/error`: working prefix invocation, expandable/attached full traceback | Not started |
| 4 | 04-settings.md (todo) | Settings panel: view everything at once, edit several keys per trip | Not started |
| 5 | 05-condensation.md (todo) | Full-surface audit: merge, hide, or gate the long tail of commands | Not started |

Phase 5 is deliberately last: merging the long tail is easier once phases 1–4 have established
the target shapes (typed-options-plus-workspace, single-entry search, panel-style settings).
