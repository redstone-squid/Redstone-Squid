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
2. ~~Type the Discord responder's native surfaces: on `sl.discord.ActionResponder`,
   override `present_form` to accept `discord.ui.Modal`.~~ **Superseded during
   implementation** — see "Why `present_form` left the protocol" below. `present_form`
   and `download` are gone from `sl.ActionResponder` and `sl.ActionEvent`; Discord grows
   a typed `send_modal`, reached through a second accessor
   `sl.discord.responder(event) -> sl.discord.ActionResponder`, which `native()` is now
   defined in terms of.
3. `ActionEvent.context`: document the reserved `"frontend"` key; do not grow the bag.
   Anything else a handler needs should come from `native()` or from host-injected
   `ContextKey`s.

## Why `present_form` left the protocol

The narrowing this plan originally specified is contravariant, not covariant, so pyrefly
rejects the Discord responder wherever the protocol is required — including
`discord/mount.py`'s own event construction. It would have cost a suppression per
construction site and bought nothing, because handlers reach the method through
`ActionEvent.present_form`, which forwards as `object` regardless.

Making the protocol generic in the form type was tried and does typecheck, but only with
an old-style `TypeVar(contravariant=True)`: PEP 695 syntax infers the event *invariant*
(the responder is a field), which makes a portable handler unregistrable on a Discord
component in either direction. It also puts `FormT` through every event, binding, handler
alias, component and mount — a large viral cost to model per-frontend form types.

The survey that settled it:

| Surface | Real usage |
| --- | --- |
| `responder.download(...)` | 0 call sites in the repo |
| `event.present_form(...)` | 8 call sites, all passing a `discord.ui.Modal` subclass |
| `ModalSpec` | 0 production consumers; `test_pagination.py` only |
| `SubmitEvent` | defined and exported, never constructed or dispatched |
| `squid_layouts/html/` | no responder at all |

So `present_form`'s portable branch never runs, portable form *submission* does not exist
even in principle, and `download` is speculation. These are Discord operations in a
portable costume, and the costume is what forced `object` — the same `object` that forced
the `getattr` + `cast` this plan exists to delete. Shedding them leaves five methods that
every frontend can honestly implement, all fully typed, with no generics, no `object` and
no suppressions.

If a genuinely portable form is ever wanted, the move is to promote `ModalSpec` to a
frontend-neutral `FormSpec` in the core and add `present_form(form: FormSpec)` back as a
typed addition to a clean protocol — cheaper after this change than before it, and it
would need `SubmitEvent` to actually be dispatched first.

Explicitly out of scope: moving permission checks into the portable surface. If a second
real frontend ever dispatches events, revisit with actual requirements
(see `90-deferred.md`).

## Migration

Replace all six `getattr` sites with `sl.discord.native(event)`; delete the local
`cast(Any, ...)`s that existed only because the interaction was untyped. Replace the
eight `event.present_form(...)` sites with `sl.discord.responder(event).send_modal(...)`.
The `cast(Any, self)` inside modal constructors is host-side typing debt, not framework
debt — fix opportunistically where touched.

## Verification

- New unit tests in `packages/squid-layouts/tests/test_native_access.py`: `native()` and
  `responder()` return the Discord surfaces, raise `LookupError` for a stub portable
  responder, and that stub still satisfies `sl.ActionResponder` statically — the property
  that stopped holding once `present_form` took a frontend object.
- Per touched consumer file, run its unit module under `tests/unit/bot/` with `--no-cov`.
- `just typecheck` — expect the six files to lose `cast` imports, not gain suppressions.
