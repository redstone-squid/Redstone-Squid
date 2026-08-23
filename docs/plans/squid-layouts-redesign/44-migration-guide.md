# 44 — Migration guide for existing discord.py bots

## Problem

CascadeUI's clearest advantage is adoption friction: it subclasses ordinary `View`/
`LayoutView`, so one stateful view can land inside an otherwise untouched bot. Squid
deliberately does not adopt a live view — renderer ownership is what keeps budget
measurement sound ([90](90-deferred.md)) — but it *does* ship every boundary an incremental
migration needs: measured fragments inside a host-owned `LayoutView`
([35](35-discord-v2-fragments.md)), classic messages ([36](36-classic-discord-target.md)),
routed controls that keep existing custom ids ([14](14-routed-actions.md),
[16](16-routed-actions-part-two.md)), forms over modals ([18](18-forms.md)), and whole-message
mounts with explicit access and sessions ([34](34-safe-session-runtime.md)).

None of that is written down as a path. The README's "three ways to adopt" paragraph and
`docs/durable-mounts.md`'s "choose the smallest lifecycle" are the seeds; a library user
arriving from a working bot has to assemble the rest from nine plan files.

## Deliverable

`packages/squid-layouts/docs/migrating.md`, ordered by how little of the bot has to change.
Docs only; no code. Each section is one legacy shape, the Squid spelling, and what does *not*
carry over.

1. **Keep your `LayoutView`, contribute a region.** `sl.discord.contribute(document, to=view,
   followed_by=...)` (`discord/fragments.py`); the two-step `fragment()` + `attach()` form;
   preflight and `FragmentOwnershipError`; why the region is stateless unless a mount owns the
   lifecycle; why `into=` and adoption are rejected.
2. **Classic messages.** `sl.discord.classic.contribute/compose/render_static`
   (`discord/classic.py`); the mode transition matrix and `DiscordModeError` from
   [38](38-discord-presentation.md).
3. **Persistent views with fixed custom ids → `Router`/`Route`.** Routes keep the custom ids
   already on posted messages; the reserved `r:` namespace and gone responses; middleware.
4. **One whole message → `Mount`.** Explicit `access=`, `MountDefaults`
   ([43](43-mount-defaults.md)), `SessionRegistry.open` with a `SessionKey` and
   `SessionPolicy`, `Rejected` handling.
5. **Modals → forms.** `FormSpec`/`sl.Form`, `SubmitEvent`, the validation retry loop.
6. **Lifecycle mapping table.** `on_timeout` → `timeout=`/`on_finish`; `interaction_check` →
   `AccessPolicy` and guards; `on_error` → `on_error`; `DynamicItem` → `Route`; ephemeral
   token expiry → `RenewEphemeral` ([39](39-ephemeral-handoff.md)).
7. **When to go durable.** Pointer to `docs/durable-mounts.md`.

Linked from `packages/squid-layouts/README.md` and
`docs/squid-layouts-architecture.md` §"Library binding".

## Verification

Every snippet is lifted from a test or a bot consumer and names it; `just i18n-extract` is
unaffected; links resolve.

## Status

Implemented 2026-08-23. Section 4 uses the `MountDefaults` spelling shipped by
[43](43-mount-defaults.md).
