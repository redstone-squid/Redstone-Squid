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
  *(Phase 3 found the prefix form does bind its argument; what it does not do is stay private.
  Both are fixed.)*
- **Settings are one-at-a-time.** `/settings set` takes a single key per invocation; first-time
  setup of a guild is a dozen round trips. No panel view, no batch edit. *(Phase 4: `/settings`
  is the panel, with a picker per key.)*
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
| 2 | [02-search.md](02-search.md) | One `/search`: taxonomies named in `scope`, tag kinds indexed, one sort option; `/patterns` removed and `/restrictions` reduced to its staff command | **Delivered** |
| 3 | [03-diagnostics.md](03-diagnostics.md) | `/error`: reports page inline, `recent` entries open in place, and every reply is private on the prefix side too | **Delivered** |
| 4 | [04-settings.md](04-settings.md) | `/settings` opens a panel: every key on one screen with a picker each, voting configured where it is displayed; `list`/`get`/`clear`/`voting show`/`voting emojis` removed | **Delivered** |
| 5 | [05-condensation.md](05-condensation.md) | Merge, hide, or gate the long tail, with audit items C2–C7 as the checklist | In progress |

**C1, picker visibility, is delivered** (2026-08-18), ahead of phase 2 as the audit
recommended. `hide_unless(...)` in `squid/bot/utils/permissions.py` wraps
`app_commands.default_permissions` with the one thing that decorator does not say out loud —
it is a visibility hint, not a gate — and nine top-level staff commands now carry it:
`/perm`, `/role`, `/settings`, `/starboard`, `/admin`, `/error`, `/restrictions` (all
`manage_guild`), `/redstoner` (`manage_roles`), and `/archive` (`manage_messages`).
`/restrictions` joined in phase 2, once its public lookup moved into `/search` and left
it a staff-only taxonomy group. Everything else stays
visible to everyone. `requires(...)` is untouched and remains the real gate; a guild admin
can override any of these per command in Server Settings, which is why each bit was chosen to
match the operation rather than defaulting everything to `manage_guild`.

Two taxonomy pins came with it: `test_staff_groups_are_hidden_from_non_staff_pickers` fixes
the whole map, so a staff group shipped visible fails CI, and
`test_subcommands_do_not_claim_a_visibility_they_would_not_get` catches the trap that Discord
accepts and then ignores `default_member_permissions` on a subcommand.

Phase 5 stays last otherwise: merging the long tail is easier once phases 1–4 have
established the target shapes (typed-options-plus-workspace, single-entry search, panel-style
settings), and C1 has already taken the pressure off it.
