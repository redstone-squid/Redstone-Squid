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
| 5 | [05-condensation.md](05-condensation.md) | Merge, hide, or gate the long tail, with audit items C2–C7 as the checklist | In progress (4 of 7 steps) |
| 6 | [06-build.md](06-build.md) | `/build`, 15 commands: one edit surface instead of two that disagree about who may edit; `queue` paginated and de-idded; the loose schematic tools moved under `build schematic` | In progress (0 of 5 steps) |
| 7 | 07-account.md (todo) | `/account`, 14 commands: identity, profile and claim management stop making you read an id off one command to type into the next | Not started |
| 8 | 08-small-groups.md (todo) | `/info` folds into `/help`; `/version`, `/redstoner` and `/tag` polish; the poll wizard's untranslated half of C3; the last C4 message arguments | Not started |
| 9 | 09-starboard.md (todo) | `/starboard`, 14 commands of CRUD across two subgroups, become a board panel | Not started |
| 10 | 10-permission-admin.md (todo) | `/role` and what is left of `/perm`: 20 staff commands, and a group name that means something else in Discord | Not started |

**C1, picker visibility, is delivered** (2026-08-18), ahead of phase 2 as the audit
recommended. `hide_unless(...)` in `squid/bot/utils/permissions.py` wraps
`app_commands.default_permissions` with the one thing that decorator does not say out loud —
it is a visibility hint, not a gate — and nine top-level staff commands now carry it:
`/perm`, `/role`, `/settings`, `/starboard`, `/records` (then named `/admin`), `/error`,
`/restrictions` (all `manage_guild`), `/redstoner` (`manage_roles`), and `/archive`
(`manage_messages`).
`/restrictions` joined in phase 2, once its public lookup moved into `/search` and left
it a staff-only taxonomy group. Everything else stays
visible to everyone. `requires(...)` is untouched and remains the real gate; a guild admin
can override any of these per command in Server Settings, which is why each bit was chosen to
match the operation rather than defaulting everything to `manage_guild`.

Two taxonomy pins came with it: `test_staff_groups_are_hidden_from_non_staff_pickers` fixes
the whole map, so a staff group shipped visible fails CI, and
`test_subcommands_do_not_claim_a_visibility_they_would_not_get` catches the trap that Discord
accepts and then ignores `default_member_permissions` on a subcommand.

Phase 5 came after 1–4 because merging the long tail is easier once those phases have
established the target shapes — typed-options-plus-workspace, single-entry search, panel-style
settings — and C1 had already taken the pressure off it.

## Why phases 6–10 exist, and their order

Phase 5 was scoped as a sweep over what no other phase owned, and its own plan defers three
groups by name: `/starboard`, `/role` plus the rest of `/perm`, and `build edit`'s flags. Each
is a phase-sized rebuild rather than a step in a sweep, and the audit's per-group findings for
`/build`, `/account` and the small groups were never claimed by anything either. Those are
phases 6–10. Nothing here is new analysis: it is [00-audit.md](00-audit.md)'s untagged
remainder plus phase 5's explicit deferrals, sliced so each phase is one surface.

Ordering is by **who is affected**, not by size. Phases 6–8 finish the *public* surface —
`build` and `account` are 29 of the 46 commands a non-staff picker still offers, and the small
groups are what is left of it. Phases 9 and 10 are staff-only groups that C1 already hides from
everyone else, so their cost is borne by the few people who can see them; that is why the two
biggest CRUD piles in the bot come last.

The surface today, for comparison with the audit's opening count: **98 commands under 18
top-level entries**, of which 9 entries are hidden from a non-staff picker. The audit measured
~108 under 19, but undercounted `/account` badly, so the real reduction is larger than the
difference suggests — and it is concentrated in what a normal user sees, which was the point.

Counts include app-only members, so `/build`'s 15 is its 14 prefix commands plus `build submit`.

| Group | Commands | Owning phase |
|-------|---------:|--------------|
| `/build` | 15 | 6 |
| `/account` | 14 | 7 |
| `/starboard` | 14 | 9 |
| `/role` | 12 | 10 |
| `/perm` | 8 | 10 |
| `/settings` | 7 | done (4) |
| `/tag` | 6 | 8 |
| `/info` | 4 | 8 |
| `/records` | 4 | done (5.2) |
| `/error` | 3 | done (3) |
| `/notifications` | 2 | done (5.3) |
| `/version`, `/redstoner` | 2 each | 8 |
| `/archive`, `/help`, `/poll`, `/search`, `/restrictions` | 1 each | — |

## Cross-cutting findings

The audit's C1–C7 are not owned by any one phase, so their status is tracked here rather than
inferred from whichever plan happened to mention them last.

| # | Finding | Status |
|---|---------|--------|
| C1 | Staff commands visible to everyone | **Done** (2026-08-18), see below |
| C2 | No ephemerality policy | Phase 5.7 |
| C3 | Replies bypassing i18n and layouts | Settings (4) and notifications (5.3) done; the poll wizard is still entirely untranslated bare strings — phase 8 |
| C4 | Message arguments where a right-click belongs | Polls done (5.1); `build recalc` is phase 6, `redstoner resync` is phase 8 |
| C5 | Raw internals in user-facing output | Search done (2); `build queue`'s submitter ids are phase 6; `records lookup`'s restriction ids and notification subject UUIDs are recorded as deliberate deferrals in [05-condensation.md](05-condensation.md) |
| C6 | Ad-hoc pagination | The paginator lands in phase 6.1, its first caller; 5.6 is the remaining call sites, and phases 7–9 use it rather than reinventing one each |
| C7 | Hybrid commands that are not | **Done** — phase 1 (`build submit-full`), phase 4 (`voting emojis`), phase 5.1 (`/poll`) |
