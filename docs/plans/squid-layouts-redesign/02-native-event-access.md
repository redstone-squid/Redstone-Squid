# 02 — Typed native event access

## Problem

`ActionEvent`/`ActionResponder` promise frontend neutrality, but any handler needing a
real Discord fact has no sanctioned way to get one. The same untyped incantation is
copied across six production files:

    interaction = getattr(event.responder, "interaction", None)
    if interaction is None or await allows(cast(Any, interaction), node): ...

Sites: `squid/bot/settings_view.py:611`, `squid/bot/account_view.py:568`,
`squid/bot/claims_view.py:402`, `squid/bot/submission/build_info.py:64`,
`squid/bot/submission/ui/views.py:1247`, `squid/bot/voting/poll_wizard.py:611`.

Adjacent symptoms of the same gap (`squid_layouts/actions.py:31-46`):
`present_form(form: object)` and `download(asset: object)` are untyped, so every modal
handoff is `event.present_form(SomeModal(cast(Any, self)))`; `event.context` is a
stringly bag currently holding `{"frontend": "discord"}`.

The portability is currently aspirational — the HTML renderer does not dispatch events —
so the abstraction pays nothing and charges every serious handler a `getattr` + `cast`.

## Design

Sanctioned, typed escape hatch rather than widening the portable protocol:

1. `sl.discord.native(event: ActionEvent) -> discord.Interaction` — new function in
   `squid_layouts/discord/actions.py`, exported from `squid_layouts.discord`. It
   isinstance-checks `event.responder` against the Discord `ActionResponder` (which
   already holds `.interaction`) and raises `LookupError` with a clear message on any
   other frontend. Handlers that must run frontend-neutrally keep using the portable
   surface; handlers that are Discord-only say so in one typed line.
2. Type the Discord responder's native surfaces: on `sl.discord.ActionResponder`,
   override `present_form` to accept `discord.ui.Modal` (the portable protocol keeps
   `object`; the covariant narrowing is safe because callers reach the typed signature
   via `native()`-style discord-scoped code or the concrete class).
3. `ActionEvent.context`: document the reserved `"frontend"` key; do not grow the bag.
   Anything else a handler needs should come from `native()` or from host-injected
   `ContextKey`s.

Explicitly out of scope: moving permission checks into the portable surface. If a second
real frontend ever dispatches events, revisit with actual requirements
(see `90-deferred.md`).

## Migration

Replace all six `getattr` sites with `sl.discord.native(event)`; delete the local
`cast(Any, ...)`s that existed only because the interaction was untyped. The
`cast(Any, self)` inside modal constructors is host-side typing debt, not framework debt
— fix opportunistically where touched.

## Verification

- New unit tests in `packages/squid-layouts/tests/test_semantic_actions.py` (or a new
  `test_native_access.py`): `native()` returns the interaction for the Discord responder,
  raises `LookupError` for a stub portable responder.
- Per touched consumer file, run its unit module under `tests/unit/bot/` with `--no-cov`.
- `just typecheck` — expect the six files to lose `cast` imports, not gain suppressions.
