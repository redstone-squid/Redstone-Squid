# Phase 4: a settings panel

> **Status.** Delivered 2026-08-19, together with this plan.

## Problem

Configuring a guild was a command per key. `settings get`, `settings set` and `settings clear`
each took exactly one setting; `settings locale` was a fourth command for a fifth key; and
`settings voting` was a five-command subgroup on top of that. Setting a server up from scratch
was a dozen round trips through a picker that shows one option list at a time.

The parts of it that were not merely slow:

- **`settings set` only understood channels.** Its `else` branch rendered "This setting is not
  supported" and then raised `AssertionError` behind the reply — a schema change would have
  produced an error report for a case the type system could have refused outright.
- **`settings list` was the only view of the whole thing**, and it was read-only: seeing that
  three channels were unset told you to go and run three more commands.
- **The voting subcommands bypassed i18n and the layout system** (audit C3). They sent bare
  strings — `await ctx.send("Voting role weight updated.", ephemeral=True)` — while the rest of
  the bot goes through `t(locale, ...)` and card layouts.
- **`voting show` printed `<@&id>`** for each weighted role, which a client renders as literal
  text whenever the role is not in its cache (audit C5).
- **`voting emojis` refused to run from the prefix side** (audit C7): it needs an interaction to
  open a modal, so it answered "Use this as a slash command" and stopped.

## Design

**`/settings` opens the panel.** The group takes a `show` fallback, so both `/settings show` and
`!settings` reach it — Discord has no bare-group invocation, and a fallback is how phase 3 gave
`/error` the same shape. The panel is `SettingsPanelView` in `squid/bot/settings_view.py`, an
`ExpiringLayoutView` holding the two services rather than a snapshot: unlike a search page, a
settings panel exists to write, and every write has to show its result.

**Two pages, because the controls do not fit one.** Components V2 allows ten top-level
components. The channels-and-language page spends eight: one card, five channel pickers, the
language select, and a navigation row. The voting page spends four: a card, the vote-kind
select, a role picker, and a button row. A test pins the ten-component budget, because a sixth
channel setting would otherwise fail only at send time.

**A picker per key, all of them live at once.** Each channel setting gets its own
`ChannelSelect`, opened on the channel it would replace via `default_values` and with
`min_values=0` — so emptying a picker is how a setting is cleared, and `settings clear` has
nothing left to do. Changing five channels and the language is now one message and six clicks
instead of six invocations.

**The language select can hand the choice back.** It offers "Follow Discord" alongside the
supported tags, writing `set_locale(None)`. `settings locale` grew the same choice, so the
scriptable path can undo what the panel can do.

**Voting is configured where it is displayed.** The voting page shows the effective emoji preset
and the role multipliers for one kind, with a kind select to switch. A role picker opens a modal
prefilled with that role's current multiplier, where an empty value removes the weight — the
`weight-set`/`weight-remove` pair collapses into one gesture, and nobody reads a role name off a
card to retype it into a second command. The emoji editor is a button on this page, which is
what retires `voting emojis`: the modal still needs an interaction, and a button is one.
Reset is armed by a first click and applied by a second, and resets the displayed kind only;
the command remains the way to reset every kind at once.

**Roles are named, not mentioned**, and a weight left behind by a deleted role shows its id so
it can still be removed. A channel a setting names but the guild no longer has says so, with the
id, for the same reason.

**The panel renders what the caller may do.** The group admits anyone holding any one of
`settings.server.view`, `settings.server.edit` or `settings.voting.edit`, so a caller granted
only vote configuration would otherwise reach a panel full of channel pickers they cannot use.
`SettingsCapabilities` is resolved once when the panel opens; edit controls are omitted rather
than disabled, and a caller with no server rights opens on the voting page. Each write re-checks
its node at click time, because a panel sits open across a revoke.

**`settings set` survives as the scriptable path**, now typed `ScalarChannelSetting` instead of
`Setting`. The two aliases are the same `Literal` today, so the `is_bearable` branch was
tautological; naming the narrower one moves the guarantee to the type checker, and the
`AssertionError` reply goes away. Omitting the channel clears the setting, which is what
`settings clear` did.

**The voting commands answer in layouts and translate.** `weight-set`, `weight-remove` and
`reset` now use `info_layout`/`error_layout` with `t(locale, ...)`, name the role rather than
mentioning it, and translate the network-scope warning. Domain validation text from
`InvalidVoteConfigurationError` stays English, as it is everywhere including the API; what the
cog says itself is translated.

## Taxonomy edits

Removed: `settings list`, `settings get`, `settings clear`, `settings voting show`,
`settings voting emojis` — five commands, all of them things the panel does better. The tree in
`tests/unit/bot/test_command_taxonomy.py` records the new shape, and
`test_the_settings_group_opens_the_panel_from_its_fallback` pins the fallback, since converting
the group would silently take `!settings` away. `settings voting` narrows from
`any(server.view, voting.edit)` to `voting.edit`, now that every command under it writes.

## Not in this phase

- **A shared paginator (C6) and the bot-wide ephemerality rule (C2).** The panel replies
  ephemerally and fits on one screen by construction; both sweeps stay with phase 5.
- **The notifications cog's raw strings.** C3's remaining half is phase 5.
- **Per-setting permission nodes.** `settings.server.edit` still gates all five channels as one.
