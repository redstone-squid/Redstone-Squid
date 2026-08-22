# 16 — Routed actions, part two: what the web routers already solved

## Problem

Plan 14 shipped in `98cf6a5d` (framework) and `f8622b03` (consumers). What landed is,
structurally, the first 200 lines of Werkzeug: a rule with named placeholders, a compiled
pattern, reverse id generation, and first-match dispatch. It is a good version of that.

But it stopped at the parts needed to delete the five hand-rolled `DynamicItem` classes,
so several problems URL routers solved decades ago were still open — and one of them was a
live correctness regression. `edit:build:(\d+)` had become `edit:build:{build_id}`, which
compiles to `[^:]+`. The pattern got *looser*: `edit:build:abc` matched, reached the
handler, and died in `int()` as a "something went wrong" reply, where the old
`DynamicItem` simply did not match.

This plan works through the web-router feature set, takes what transfers, and records what
does not so the rejected half is not re-derived.

## Mapping

| Web-router feature | Verdict |
|---|---|
| Typed path converters (`<int:x>`, `{x:int}`) | **Taken** — stage 1 |
| Params as handler arguments (Flask kwargs) | **Taken** — stage 2 |
| Signature-preserving decorators (ParamSpec) | **Taken** — stage 2 |
| Route declared inline as a string (`@app.route("/x/<int:y>")`) | **Taken** — stage 2 |
| Lowercase `router` / `app` module singletons | **Taken** — stage 0 |
| Typed request/app context | **Taken** — stage 3 |
| Redirects / aliases (301) | **Taken** — stage 4 |
| 404 handler | **Taken** — stage 4, scoped; see below |
| Middleware / `before_request` | **Deferred** — stage 5 did not survive Discord semantics |
| "A view must return a response" | **Deferred** — the raw interaction exposes no reliable completion signal |
| Method verbs (GET/POST) | **Taken** — stage 6; the analogue is *component type* |
| Form body vs path params | **Taken** — stage 6; selected values are a second parameter source |
| `flask routes` / `show_urls` | **Taken** — stage 7 |
| Blueprints / `include_router(prefix=)` | **Deferred.** Real at 30 routes; at 5 it adds indirection over one module-level `router` |
| `Depends` dependency injection | **Rejected.** The duplication (`resolve_locale`, `_authorize`) is bot-domain, not framework. Stage 5's middleware is the seam to build it host-side |
| `url_for("endpoint")` string keys | **Rejected.** A `Route` object is the typed, refactor-safe version of the same thing |
| Werkzeug specificity-ordered matching | **Rejected.** Registration order is loud; specificity sorting makes an overlap silent. First-match plus the probe check stays |
| Trie / radix compiled dispatch | **Rejected.** n=5, and discord.py forces one regex template regardless |
| Query strings, optional params | **Rejected.** 100-char budget. The tier's premise is "point at a stored row, don't encode one" |
| Greedy tail segments (`<path:x>`) | **Rejected.** Same budget argument; `:` stays an unambiguous separator |
| Signed URLs / tamper-proofing | **Rejected, documented in `routing.py`.** Discord only echoes back an id that already exists on a message it rendered, so ids cannot be forged. They are readable by anyone who can see the card, so handlers still authorize per click |
| Test client (`app.test_client()`) | **Already had it.** `discord/testing.py`'s `fake_interaction` plus `Router.dispatch` is the test client; now said so in the module docstring |

### Why a 404 needs a namespace

A web router owns its entire URL space, so an unmatched path is unambiguously a 404. This
router does **not** own the custom-id space — it shares it with `Mount`
(`ctl:{mount_id}:{generation}:{key}`) and with any hand-written `discord.ui` component. A
`.*` fallback branch would swallow every mount button.

So the framework can only answer "this control is gone" inside a prefix it reserves. The
fallback branch is safe *because* plan 14 chose one `DynamicItem` over one per route:
discord.py schedules one call per matching class, and alternation branches inside a single
compiled template do not multiply.

---

## Shipped

### Stage 0 — Naming (`1456a38e`)

`ROUTER`, `POLL_CLOSE` and friends are objects with behaviour, not constants. The repo's
own HTTP layer had already settled this — ten `router = APIRouter(...)` declarations under
`squid/api/v1/` — and ruff has pep8-naming commented out of `select`, so nothing enforced
the uppercase. `app.py` aggregates rather than declares, so it takes the same
`import router as <name>_router` form the v1 API package uses at its own aggregation site.

**Rejected: attaching the route to the handler** (`@router.route("...")` with reverse via
`edit_build.route.id(...)`, the typed cousin of Django's `reverse()`). One declaration site
instead of two is tempting, but `build_info.py` would then import from `ui/components.py`,
which already carries a `# FIXME: circular import`. `routes.py` exists precisely so "the
card that draws a button and the code that answers it cannot drift".

### Stage 1 — Converters, and one validation rule instead of three (`bc79590c`)

A parameter's format spec names its converter, the way Werkzeug spells `<int:build_id>`.
The spec slot was previously rejected outright, so nothing had to move to make room, and
`int` renders exactly as the old format did — ids are unchanged and posted cards are
unaffected.

This also collapsed three checks into one. `id()` tested for an empty value, a value
carrying the separator, and the length budget; the first two are special cases of the
invariant the whole tier rests on:

> A route's pattern matches every id that route builds.

Asserting that directly lets empty, separator-bearing and wrong-typed values all fail as
"not one of my ids", and turns the build/match symmetry from a docstring claim into a
checked one.

Errors split by whose invariant broke. Using a route wrong is a `ValueError`, matching what
`__post_init__` already raised for a malformed format; only Discord's 100-character limit
stays a `LayoutInvariantError`, because that one really is a layout limit.

The router's overlap probe moved onto the converter, since `"\x01"` cannot satisfy an `int`
field. `str` keeps `"\x01"` for the reason it was chosen: it cannot occur in a route
literal, so a probe collision means a real overlap.

### Stages 2 + 3 — Handler parameters and the client type (`8e8c6150`)

Landed together because `Concatenate` needs the client type parameter to pin the handler's
first argument.

Handlers take their parameters the way a Flask view takes its path variables, and `add`
checks the names against the route at registration — an import error, stricter than Flask,
where the same mistake waits for the first request. A handler that wants none declares
none; one taking `**kwargs` gets everything.

`Router[BotT]` plus `Concatenate` pins the first argument to `Interaction[BotT]`, so
`give_redstoner` stops reaching `interaction.client.owner_server_id` through `Any`, and the
two `bot: RedstoneSquid = interaction.client` re-annotations in `voting/controls` are
deleted rather than moved. `routes.py` annotates the router instead of subscripting it,
since `app` imports that module and PEP 649 defers an annotation but not an expression.

`route()` also accepts the format string directly. Naming a `Route` only pays when
something outside the handler's module builds ids from it — the usual case here, but not a
rule worth enforcing.

Two findings worth keeping:

- **The registration check must read signatures with `annotationlib.Format.FORWARDREF`.**
  Evaluating a handler's annotations resurrects the TYPE_CHECKING-only client import at
  import time, which is precisely what PEP 649 defers. Only names and kinds are wanted.
- **How far the typing propagates.** ParamSpec preserves the decorated signature, so a
  direct call in a test is checked, and `Concatenate` constrains the first argument — both
  verified against `pyrefly --config pyproject.toml`, which catches a handler annotating
  the wrong client and a call passing `str` where the route yields `int`. ParamSpec cannot
  check parameter *names* against a format string no checker parses, which is why the
  runtime check exists.

## Corrections from external review

An outside review of plan 14 raised seven points. Each was checked against the shipped
code rather than reasoned about; two were already handled, one is moot, and four were real.
C1-C4 have since shipped.

### Already handled

**Routed actions must not fold into a select, and planning must fail honestly.** Correct,
and this is how plan 14 shipped. `planning/adaptation.py` routes `Link` and `RoutedAction`
into a `direct` list that becomes plain buttons, never `_picker()` — a routed control's
identity is its own custom id, and a select would replace that with
`(select custom id, option value)`, which is a different operation. 35 routed actions
raise `UnsolvableLayoutError` ("42 components exceed target maximum 40") rather than
silently changing transport.

**`register()` must freeze the table.** It does: `_registered` makes a genuinely new route
after registration a `RuntimeError`. Re-registering an *existing* format is deliberately
still allowed, because loading an extension re-executes its module and a reload must keep
working; the route set is unchanged, so the generated template cannot go stale.

**Legacy `DynamicItem` classes coexisting with the router.** Moot — `f8622b03` deleted all
five, and no `DynamicItem` subclass remains in `squid/`. A guard against two `Router`
instances registering on one client is still absent, but with no second router in the tree
this is hardening, not a live hazard.

### C1 — Route overlap detection is not exact (`21aac8c1`)

Was open; fixed. The probe check compares one sample id per route, so a mid-segment
intersection escapes it:

    a = Route("foo:{x}:baz")     # matches foo:bar:baz -> {"x": "bar"}
    b = Route("foo:bar:{y}")     # matches foo:bar:baz -> {"y": "baz"}
    router.add(a, h); router.add(b, h)   # both accepted today

`foo:bar:baz` then dispatched to whichever registered first, silently.

The fix was to stop trying to decide regex intersection and shrink the grammar instead: a
route is colon-separated segments, each one *exactly* a literal or `{name}` /
`{name:conv}`. All five production routes already satisfy that, as do the stage 4
namespaced formats. Overlap then decides exactly, per position:

- literal vs literal — overlap iff equal
- literal vs parameter — overlap iff the converter's pattern matches the literal, so
  `remove:role:redstoner` and `remove:role:{id:int}` are correctly disjoint
- parameter vs parameter — overlap (`int` ⊂ `str`, and every converter's language is
  non-empty)

with routes of different segment counts trivially disjoint. Ambiguity is now structurally
impossible rather than order-dependent, so `add` rejects it and `resolve`'s first-match-wins
is a formality.

Two notes from doing it. Splitting has to respect braces, since `{build_id:int}` carries a
separator of its own — the naive `str.split` broke the very format stage 1 added. And the
literal-vs-parameter rule has to consult the converter, or `remove:role:redstoner` and
`remove:role:{id:int}` would be refused as overlapping when they are disjoint.

Still available if the table ever grows: a literal/parameter trie instead of N regexes
re-run after the master template matches. Not worth it at five routes.

### C2 — A routed click extends its mount's timeout (`ab4969e1`)

Was open and live: `submission/ui/views.py:1284` puts a routed Edit button in a
`LayoutView`.

discord.py's `dispatch_view` calls `dispatch_dynamic_items` *and then* looks the item up in
the stored view. Plan 14 checked that the second path cannot double-respond — the stored
item is a plain `Button` whose callback is `Item`'s no-op — but not that it is inert.
`BaseView._scheduled_task` runs `self.__timeout_expiry = time.monotonic() + self.timeout`
*before* awaiting that no-op. With `Mount`'s default `timeout=900`, a routed click therefore
keeps the surrounding mount alive, and the mount's own `_active` does not move, so devtools
under-report the mount's age.

So plan 14's "dispatch bypasses the mount's funnel entirely" is too strong: it bypasses
`Mount.dispatch`, not discord.py's stored-view machinery.

Fixed with `sl.discord.RoutedItem`, a `discord.ui.Button` subclass overriding
`is_dispatchable()` to return `False`. `ViewStore.add_view` only files an item into `dispatch_info` when
`is_dispatchable()` is true, so the outgoing button is never stored and there is exactly
one dispatch path. Dynamic dispatch is unaffected: it rebuilds the view with
`LayoutView.from_message`, which produces stock `Button`s, and finds the base item by
`component_type + custom_id` there. The wire payload is an ordinary button either way.

One consequence found while implementing: `store_view` is called only when the *view* has
some dispatchable child, and `add_view` is what starts the timeout task. A document of
nothing but routed controls would therefore have stopped being stored and never timed out,
so `MountedView.is_dispatchable()` answers True for itself regardless of what it draws.

The hand-built button at `submission/ui/views.py` needed the same treatment; it does not go
through the renderer.

### C3 — The custom-id budget is bypassable (`b6001481`)

Was open:

    RoutedButton("Edit", "x" * 500)   # renders through render_static, no complaint

`Route.id()` checks `LIMITS.custom_id`, but a hand-built or codec-deserialized
`RoutedButton` never passes through it, and `conform` does not check custom-id length —
only `discord/testing.py`'s `payload_problems` does, which is a test helper. So an invalid
state is representable and surfaces as a 50035 at send time, which is exactly the hole the
planner exists to close.

Validated twice now: `Route.id()` keeps its early friendly error, and `conform` enforces the
invariant regardless of how the node was constructed, covering `Button` and select custom
ids alike. Reported, never clamped — every other string there degrades acceptably when
trimmed, but a shortened custom id routes to a different handler or to none.

### C4 — `custom_id` is Discord vocabulary in a portable protocol (`52e983a7`)

`SceneRoutedButton.custom_id` and the codec's `"custom_id"` key put a frontend's word in
the scene protocol. `route_id` is the honest name: Discord maps it to `custom_id`, HTML can
emit `data-route-id`, and the semantic layer keeps saying "opaque stable routed-interaction
identity".

Shipped as `route_id` throughout the portable and semantic layers. Discord maps it to
`custom_id`; HTML emits `data-route-id`. The experimental scene protocol remains version
1 and was rewritten in place, with no legacy decoder for a protocol that had no supported
stored population.

## Shipped since the original plan

### Stage 4 — Aliases and a reserved namespace (`602fa32e`, `c5404634`, `4d4af451`)

**Aliases (301).** `Route(format, aliases=(...))`. Alias patterns join `anonymous` for the
template and are tried in `match`, but `id()` always builds the *canonical* format, so a
renamed route keeps answering already-posted buttons while new cards get the new id.

**Namespace (`r:`) + gone-handler (410).** New routes authored under a reserved prefix;
`register` appends one fallback branch scoped to it, and `dispatch` answers an unmatched id
inside the namespace with a friendly reply instead of Discord's "This interaction failed".
`add()` rejects every route whose accepted language enters `ctl:`, using exact segment
intersection rather than a synthetic probe, so canonical formats and aliases are covered
by the same generalized C1 fix. Alias-to-alias and alias-to-canonical overlap are likewise
checked exactly. Button and select registrations form separate identity spaces, matching
their role as method verbs.

Agreed 2026-08-21: migrate all five routes into the namespace, with legacy aliases, so the
posted population drains into it on its own. The instruction was "don't worry about
backcompat"; the aliases deliver it cheaply enough to keep anyway, and the alias mechanism
stays regardless as the general answer to a future rename.

All five production identities now live under `r:` and retain their old ids as aliases.
The production identity tests pin both spellings, including `edit:build:5`, so a later edit
cannot silently orphan posted controls. Unknown `r:` controls receive a localized gone
response; `ctl:` remains wholly reserved for mounts.

### Stage 6 — Explicit stateless selects (`0fe0cca9`)

`RoutedChoices` lowers to one `RoutedSelect`, never through the session-bound choice
collapse or pagination ladder. Its route id remains stable and its selected string values
arrive before typed path parameters at an explicit `@router.select` handler. Buttons and
selects may intentionally share a route id because component type is part of registration
and resolution identity.

The portable node, scene codec/schema, Discord renderer, HTML renderer and planner all
carry the distinction. More than 25 routed options fails with a remedy to split the picker
into separate routes; the planner never invents state or silently changes transport.

### Stage 7 — Introspection (`4a705e11`)

`Router.describe()` returns immutable public descriptions containing component type,
canonical format, parameter converters, aliases, and handler provenance. Owner-only
`!dev routes` renders the live table privately alongside `!dev ui`.

## Registration hardening (2026-08-21 external audit; shipped 2026-08-22)

Two dispatch-boundary gaps, both verified in-repo and since closed — `register` now keeps a
weak per-client router list (same pair is a no-op, an id-language intersection with an
already-installed router is rejected, namespaces included), and `_accepted` checks kinds:
leading parameters must bind positionally and positional-only parameters beyond them are
rejected at registration. Covered by `TestClientRegistration` and `TestHandlerKinds` in
`test_routing.py`. The original findings:

- **`Router.register` is not idempotent per client.** Every call builds a fresh
  `RoutedDispatch` class over the same template (`discord/routing.py:287`); the same
  client registered twice holds two dynamic items with identical regexes, and
  discord.py's `ViewStore` schedules one call per matching class — the double-fire the
  one-class design exists to prevent. Multi-client use stays supported (test suites
  build a bot per case). Fix: a weak client registry — the same (client, router) pair is
  a no-op, and a second router whose accepted language overlaps one already registered
  on that client is rejected with the same exact-intersection check `add()` uses.
- **`_accepted` checks names but not kinds** (`discord/routing.py:87`). It never
  verifies that the first `required` parameters are positionally bindable — a
  keyword-only `interaction` registers and dies at dispatch — and the `named`
  comprehension silently drops `POSITIONAL_ONLY` parameters, so
  `def h(interaction, build_id, /)` registers with `accepts = ∅` and fails on the first
  click. Both violate this plan's fail-at-registration rule. Fix: require the leading
  parameters to be positionally bindable, and reject `POSITIONAL_ONLY` parameters
  beyond them rather than ignoring them.

## Deferred

### Stage 5 — Middleware, declarative defer, and the response guarantee

The proposed `defer="ephemeral"` abstraction is not an honest button default. For a
component interaction, `defer(ephemeral=True)` defaults to a deferred message update;
`ephemeral` only affects a new deferred response when `thinking=True`. A generic router
policy would therefore need to choose between editing the clicked message and showing a
private loading state without knowing the handler's intent.

The proposed response guarantee is also not observable from a raw interaction. The router
can see whether the initial response was acknowledged, but followups and edits are not a
single completion bit it can reliably inspect. Middleware would either misdiagnose valid
handlers or require wrapping every response path, turning a small dispatch layer into a
second interaction API. Keep defer, authorization, and response ownership explicit in the
handler until a concrete repeated policy justifies a narrower abstraction.

## Verification

Stages 0-4, 6-7 and C1-C4 have automated coverage in the package and bot unit suites. The
remaining operational check is to click a vote-card button and a build-card Edit button
posted before the namespace migration, confirming legacy aliases on real Discord messages
in addition to the pinned synthetic identities.
