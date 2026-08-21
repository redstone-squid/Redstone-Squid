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
| Redirects / aliases (301) | Stage 4 |
| 404 handler | Stage 4, scoped — see below |
| Middleware / `before_request` | Stage 5 |
| "A view must return a response" | Stage 5 |
| Method verbs (GET/POST) | Stage 6 — the analogue is *component type* |
| Form body vs path params | Stage 6 — the analogue is `interaction.data["values"]` |
| `flask routes` / `show_urls` | Stage 7 |
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
C1-C3 have since shipped; C4 remains.

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

### C4 — `custom_id` is Discord vocabulary in a portable protocol

`SceneRoutedButton.custom_id` and the codec's `"custom_id"` key put a frontend's word in
the scene protocol. `route_id` is the honest name: Discord maps it to `custom_id`, HTML can
emit `data-route-id`, and the semantic layer keeps saying "opaque stable routed-interaction
identity".

Cosmetic, and it is a codec-format change, so it belongs with stage 4's format churn rather
than on its own.

## Not shipped

### Stage 4 — Aliases and a reserved namespace

**Aliases (301).** `Route(format, aliases=(...))`. Alias patterns join `anonymous` for the
template and are tried in `match`, but `id()` always builds the *canonical* format, so a
renamed route keeps answering already-posted buttons while new cards get the new id.

**Namespace (`r:`) + gone-handler (410).** New routes authored under a reserved prefix;
`register` appends one fallback branch scoped to it, and `dispatch` answers an unmatched id
inside the namespace with a friendly reply instead of Discord's "This interaction failed".
`add()` rejects a route that shadows `ctl:` — probe a synthetic mount id against the new
route's pattern — so a route can never eat a mount's buttons.

Agreed 2026-08-21: migrate all five routes into the namespace, with legacy aliases, so the
posted population drains into it on its own. The instruction was "don't worry about
backcompat"; the aliases deliver it cheaply enough to keep anyway, and the alias mechanism
stays regardless as the general answer to a future rename.

Needs an **alias identity guard**: a test asserting each production route's alias still
builds the exact pre-migration id (`poll:close`, `edit:build:5`, …), so the rename is
provably non-orphaning and a later edit cannot silently undo it.

### Stage 5 — Middleware, declarative defer, and the response guarantee

- `@router.route(x, defer="ephemeral")`, covering the hand-rolled
  `await interaction.response.defer(ephemeral=True)` at the top of `consent_banner` and
  `give_redstoner`.
- A Starlette-shaped chain, `Callable[[Interaction, Next], Awaitable[None]]`. The framework
  ships the seam only; the bot builds `@owner_guild_only` (open-coded in `give_redstoner`)
  and its locale/authorize decorators on top. An interaction has one initial response and a
  ~3s ack deadline, so middleware here is *not* transparent the way HTTP middleware is.
- **The response guarantee.** Flask errors when a view returns `None`. `give_redstoner`
  defers and then bare-`return`s on two guard paths, leaving an ephemeral "thinking" state
  with nothing behind it. After the handler, if the router deferred and nothing was sent,
  log it and send a generic followup. Confirm the exact Discord behaviour when
  implementing rather than trusting this description.

### Stage 6 — `RoutedSelect`: the missing verb

Only buttons are routable, which is why `planning/adaptation.py` carves routed controls out
of the collapse ladder:

> Links and routed controls carry no binding, so they can never be folded into a select
> menu the way a group of session actions can: they stay individual buttons.

A `RoutedSelect` closes it: a stateless select whose custom id is a route and whose chosen
values arrive as a second parameter source — path params from the id, "form body" from
`interaction.data["values"]`, exactly the web split. Touches `primitives/nodes.py`,
`semantic.py`, the three `scene/` modules, both renderers, and lifts the carve-out.

Largest stage, and the only one whose demand is inferred rather than observed. Confirm a
real card wants a stateless select before starting it.

### Stage 7 — Introspection

`Router.describe()` → the route table (canonical format, params and converters, aliases,
handler qualname, defining module), rendered by a `routes` subcommand alongside plan 13's
`!dev ui`. Five lines of framework, and the thing that makes stage 4's namespace migration
auditable.

## Verification

Stages 0-3 and C1-C3 verified: `packages/squid-layouts/tests` (408), `tests/unit/bot`
(647), `pyrefly --config pyproject.toml` at 0 errors. `tests/unit` as a whole is CI's job.

Remaining stages want, on top of their own unit tests: the alias identity guard above, and
a manual pass via the `run` skill after stage 4 — click a vote card button and a build
card's Edit button that were posted *before* the rename, to confirm the alias path works on
a real message rather than a synthetic id.

## Sequencing

0-3 landed together, then C1-C3. Remaining order: **5, then 4 (with C4), then 7, then 6** —
5 before 4 because the middleware seam makes the namespace's gone-handler a one-liner; C4
rides along with 4's format churn since both change the codec; and 6 stays last because its
demand is the least established.
