# Command surface audit

> **Status.** Findings inventory, compiled 2026-08-17 by reading every cog with a UX eye. This
> expands the complaint list in [README.md](README.md); each finding is tagged with the phase
> that should absorb it. Phase 5 (condensation) owns anything untagged.

The surface today: **~108 commands across 19 top-level groups** (`build` 14, `settings` 11,
`starboard` 14, `role` 12, `perm` 10, `notifications` 8, `account` 7, `tag` 6, `admin` 4,
`info` 4, plus `search`, `restrictions`, `patterns`, `vote`, `poll`, `version`, `error`,
`redstoner`, `help`, `archive`). A typical non-staff user needs perhaps eight of them.

## Cross-cutting findings

- **C1 — Staff commands are visible to everyone.** No command sets
  `app_commands.default_permissions`; gating is entirely runtime `@requires(...)` nodes. So
  every user's command picker shows all ~108 commands — `/perm`, `/role`, `/starboard`,
  `/admin`, `/error`, `/redstoner` included — and the wrong ones fail only after invocation.
  This single fact produces most of the "too many commands" feeling. Fix is cheap relative to
  impact: set `default_permissions` (and/or `guild_only`) on staff groups so the picker shrinks
  to what the viewer can actually run. Runtime nodes stay as the real gate. **Done
  2026-08-18**, before phase 2: see [README.md](README.md) for the eight commands and the
  permission bit each one claims.
- **C2 — Ephemerality has no policy.** Three patterns coexist: always-ephemeral (72 sites),
  `ephemeral=ctx.interaction is not None` (11 sites), and always-public. Near-identical
  commands differ: `account approve-claim` answers publicly, `account claims` ephemerally.
  Decide a rule (mutations of shared state public, personal/staff reads ephemeral, errors
  ephemeral) and apply it once.
- **C3 — Some replies bypass i18n and the layout system entirely.** The `settings voting`
  subcommands, the notifications cog, and parts of the poll flow send raw untranslated strings
  (`await ctx.send("Voting role weight updated.", ephemeral=True)`) while the rest of the bot
  goes through `t(locale, ...)` and card layouts. *(Phase 4 for settings; phase 5 sweep for the
  rest.)*
- **C4 — Message-argument commands fight the slash UI.** `poll close`, `poll refresh`,
  `vote delete`, `build recalc`, `redstoner resync` all take a `discord.Message`, which in
  slash form means pasting a message link. These are right-click actions; only build edit has a
  context menu today. Adding context menus (Discord allows 5 per app — budget them) would let
  several commands disappear.
- **C5 — Raw internals leak into user-facing output.** `build queue` prints the submitter's
  numeric Discord ID instead of a mention or name; `restrictions search` prints
  `restriction_id: name` lines; `patterns search` prints match scores; `admin records-lookup`
  demands comma-separated numeric restriction IDs as input; notifications display bare UUIDs.
- **C6 — Pagination is ad hoc.** `search` has a real paginator; `build queue` renders
  everything into one card (overflow risk); `version list` truncates at 20 with a TODO;
  `account claims` caps at 10 with a "N more not shown" footer; `admin records-gaps` caps
  at 30. One shared list-paginator applied everywhere would retire three bespoke truncation
  schemes.
- **C7 — "Hybrid" commands that aren't.** Several hybrid commands immediately bail without an
  interaction ("Use the slash command to open the editor"): `poll create`,
  `settings voting emojis`, and (until phase 1) `build submit-full`. Each should either work
  from prefix or be declared app-only, so the taxonomy stops advertising entry points that
  refuse to run.

## Per-group findings

### `/search`, `/restrictions`, `/patterns` *(phase 2)*
- Four entries into one question (`search`, `restrictions search`, `patterns search`,
  `patterns list`), as already recorded. Both niche `search` commands are redundant with the
  autocomplete their own option already has: typing into an autocompleted field *is* the
  search.
- `patterns search` output is `name (score: 12.3)` — the raw ranking score means nothing to a
  reader (original complaint).
- `/search` exposes `scope`, `mode`, `sort`, `direction` as four separate enums before the
  query. `mode` (keyword vs smart) is a retrieval mechanic, not a user question; `sort` +
  `direction` could be one option ("width ↑"). Consider folding filters into the query
  language (`width:5` already exists) and demoting the enums.
- `restrictions add-alias` is a staff taxonomy edit living in a public lookup group.

### `/error` *(phase 3, done 2026-08-19)*
- ~~**Root cause of the prefix complaint found:** `hybrid_group(name="error", fallback="show")`
  without `invoke_without_command=True`.~~ **Wrong.** `HybridGroup.__init__` sets that flag
  unconditionally, and `Group.invoke` rewinds the argument view when the first word is not a
  subcommand, so `!error <ref>` has always bound `reference`. The prefix form's actual defect
  was the opposite one: it worked, and `Context.send` drops `ephemeral` without an interaction,
  so it posted the traceback into the channel. See
  [03-diagnostics.md](03-diagnostics.md).
- The full traceback *is* already attached as `error-<ref>.txt` with the log tail, with a
  1200-char inline preview. If that still reads as "can't expand", the fix is interaction
  design (expand button / paged inline view), not data availability. *(Paged inline view, with
  the log tail paging after the traceback.)*
- `error recent` lists references but offers no way to open one — each line should be
  clickable (select/buttons) instead of making the user retype the reference. *(Select.)*

### `/settings` *(phase 4)*
- `get`/`set`/`clear` operate on exactly one key per invocation, and `set` only understands
  channel settings — anything else hits an error branch with an `AssertionError` behind it.
  `locale` is a separate command; `voting` is a five-command subgroup. First-time guild setup
  is a dozen round trips.
- Target shape: `/settings` opens a panel (Components V2) showing every setting with channel
  selects, a locale select, and a voting section; `settings set` survives as the scriptable
  path. The voting emoji modal already points the right direction.
- The voting subcommands are the C3 offenders (raw strings, no i18n, no layouts) and
  `voting show` renders raw `<@&id>` mentions in an ephemeral message, which display as plain
  text when the role cache misses.

### `/build` *(mostly done in phase 1; leftovers)*
- `build queue`: prints raw `submitter_discord_id`, no pagination (C5, C6), and titles the
  card "Open Records" while the command says "pending submissions".
- `build edit` is gated on `build.submission.edit`, so a submitter cannot invoke it on their
  own pending build — yet the edit *button* on the submission preview allows exactly that via
  `BuildEditView.can_edit`. Same operation, two authorization answers depending on entry
  point. Align the command gate with the view's owner-or-node rule.
- Two divergent edit surfaces: the 22-flag `build edit` command (near the 25-option cap) and
  the interactive `BuildEditView`. After phase 1 the same consolidation argument applies:
  typed options for the autocompleted fields, the view for everything else.
- `build recalc` takes a `discord.Message` (C4 — context menu candidate).
- `build debug` dumps `str(build.__dict__)` into a message; an attached JSON file would
  survive size limits and be readable.
- `measure-timing` and `detect-lattice` are schematic tools living directly under `build`
  while four other schematic tools live under `build schematic` — move them in.

### `/vote`, `/poll` *(phase 5)*
- `vote` retains two members: the deprecated `vote poll` alias and `vote delete`. Retire the
  alias, move deletion votes to a context menu or `/poll delete-message`, and the whole `vote`
  group disappears.
- `poll close`/`poll refresh` take message links (C4); both are also buttons-on-the-poll
  candidates, which would empty the `poll` group down to `create`.

### `/account` *(phase 5)*
- Self-service (`link`, `unlink`, `refresh`, `claim`) and staff review (`claims`,
  `approve-claim`, `reject-claim`) share one public group. The review trio should be buttons
  on the `claims` list view — staff currently read a claim ID off a card and retype it into a
  second command that already autocompletes it. With C1, the split becomes invisible anyway.

### `/notifications` *(phase 5)*
- `consent` (accept notice + choose channels) and `channels` (choose channels) are
  near-duplicates; one settings-style command or panel covers both.
- `follow-creator`, `follow-record`, `follow-records` are three commands for "follow
  something"; one `follow` with a kind choice (or just the structured filter form) reads
  better in the picker.
- Replies are raw strings (C3); `list` prints UUIDs (C5); `unfollow` autocompletes ids that
  `list` makes you read manually — same button-instead-of-retype shape as account claims.

### `/admin` *(phase 5)*
- The group is named `admin` but contains only record-computation tooling (`records-gaps`,
  `records-title-issues`, `records-rebuild`, `records-lookup`). Either it becomes `/records`
  (staff-gated via C1) or the commands move under `build`. The `records-` prefix on every
  member is the group name it actually wanted.

### `/perm`, `/role` *(phase 5)*
- 22 staff commands, the two biggest groups after `build`. `whoami`, `test`, and `explain` are
  three ways to ask "what can I do" — one command with an optional node argument covers all
  three. The `role` group name collides with Discord's own roles in conversation; it manages
  permission-role objects. Low urgency once C1 hides them from non-staff.

### `/starboard` *(phase 5)*
- 14 commands including `emoji`/`weight` subgroups of pure CRUD. A `starboard show`-style
  panel with edit controls could absorb most of it. Staff-only; C1 first, then decide how much
  is worth rebuilding.

### `/info`, `/version`, `/redstoner`, `/help` *(phase 5)*
- `info` is four static links; a single `/links` (or a section in `/help`) suffices.
  `info form` points at the legacy Google form and should retire or clearly mark itself
  legacy now that `/build submit` is the path.
- `version list` truncates at 20 with a TODO (C6); `version add` is staff-plus-listener and
  fine.
- `redstoner panel`'s callback is literally named `abc` (cosmetic, but it will bite grep).
- `help` duplicates part of `info`'s job already; fold link discovery into it.

## Suggested attack order

1. **C1 (picker visibility)** — one decorator per staff group, biggest perceived-surface win
   per line of code, and it de-risks phase 5 by shrinking what actually needs merging.
2. Phase 2 (search), which retires two groups outright.
3. Phase 3 (`/error`), a two-line root-cause fix plus an interaction polish.
4. Phase 4 (settings panel), the largest new UI build.
5. Phase 5 sweeps with C2/C3/C4/C5/C6 as its checklist.
