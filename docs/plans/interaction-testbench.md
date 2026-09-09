# Interaction test bench

## The gap this fills

There is no simulated Discord. `squid_ui_discord.testing.interaction_harness` exercises a
routed handler offline, but nothing exercises the parts a fake cannot carry: Discord's real
`interaction.response` lifecycle, the 2.5 s acknowledgement watchdog, modals, ephemeral
delivery, permission checks against a real member, and the durable dispatch path a button
takes after the process that drew it has restarted. Today the only way to reach those is to
recreate the production situation by hand — post a build, open a poll, trip the consent
banner — and click.

The observation: a button click *is* a real interaction, and one interaction is enough to
bootstrap any flow. A handler given that interaction can open the real build editor, post
the real poll card, or present the real consent prompt, and from there Discord generates
every further interaction itself. So the bench is not a test runner; it is a **seed
dispenser** — an owner-only message whose buttons hand a raw interaction to a named function.

## Why routed, not mounted

A layout-engine mount (`sl.Component` + `MessageRoot`) owns its interaction: author lock,
generation check, transaction, and the decision of when to defer or edit. A "Fire" button
inside a mount would hand probes a pre-owned interaction, which is exactly the thing that
cannot be tested that way — no modal, no free `response`, no leaving it unanswered to watch
the watchdog.

The durable router (`squid/bot/routes/_root.py`) is the other tier: any component whose
custom id is under `r:` reaches a handler taking the **raw** interaction, through the real
middleware chain, watchdog and `on_error` hook. Building the bench there means:

- every click is the production dispatch path, not an approximation of it;
- the bench message is durable — it survives restarts and can be clicked indefinitely,
  with no view timeout and no persistent-view registration;
- an exception in a probe reaches `handle_interaction_error` for real, so the error card
  is under test rather than imitated;
- `/tests <custom_id>` generalises for free to any id at all, including aliases and
  retired ids.

## Shape

One development-only extension, `squid.bot.testbench`, added to `DEVELOPMENT_EXTENSIONS`.

### Route group

```
r:bench:{name}           button  -> run probe `name`
r:bench:{name}:pick      select  -> run probe `name` with the chosen values
```

Defined through `_feature_group("bench")` / `_feature_route` like every other feature, so a
reload keeps the identities. An `OwnerOnly` middleware on the group answers a non-owner
press with a personal "Owner only." notice and stops; probes are arbitrary code on a public
button, so this gate is the one non-negotiable part.

The handler resolves `name` in a probe registry, times `await probe(interaction, values)`,
and then does exactly one thing: if the probe returned **without** answering the
interaction, it replies personally with `name · ok · 12 ms`. If the probe answered, its
answer is the result. Exceptions are not caught — the router's `on_error` is part of what
is being tested. An unknown `name` (a probe removed since the message was drawn) replies
personally that the probe no longer exists.

### Probe registry

```python
@probe("raise", "Raise inside a routed handler; expect the error card.")
async def raise_error(interaction: Interaction[RedstoneSquid], values: tuple[str, ...]) -> None:
    raise RuntimeError("bench")
```

A probe is an `async (interaction, values) -> None`; `values` is empty from a button. The
decorator records name and one-line purpose. The registry is a module-level dict, so adding
a probe is adding a function — no wiring.

Initial probes, chosen because each reaches a path nothing offline can:

| name | reaches |
| --- | --- |
| `raise` | `on_error` → `handle_interaction_error`, surface `route:r:bench:{name}` |
| `slow` | sleeps past the watchdog, then responds — the respond-after-managed-defer path |
| `personal` | `app_ui.respond(..., audience="personal")` on a raw interaction |
| `locale` | echoes what `sd.request(interaction)` resolves for this member and guild |
| `form` | opens a modal through `Request`; the submit arrives as its own real interaction |
| `consent` | `open_consent_prompt(interaction)` — the full ephemeral consent flow |
| `editor` | `open_build_editor` for a fixed build id, the same way the routed card does |
| `echo` | replies with `values`; exists so the select route has something to show |

### The `/tests` command

A hybrid command, `/tests [custom_id]`, checked by `is_owner` (and `cog_check` refusing
outside development mode, as `squid.bot.devtools` does).

- **No argument**: posts one static message via `render_payload` + `sd.send_to`, the way
  the consent banner is posted. Sections:
  1. *Probes* — a `RoutedButton` per registered probe, labelled by name, and one routed
     string select over the probe names for the `pick` route.
  2. *Routes* — a `RoutedButton` per zero-parameter route in `router.describe()`, id
     built by `Route(format).id()`. This is the "click the real handler from a message
     that is not its card" section: `r:polls:close` from here hits the not-found branch,
     which is a branch worth having a button for.
  3. *Gone* — one button with an owned-by-nobody id in the namespace (`r:gone:bench`), so
     the gone hook has a button too.
- **With `custom_id`**: posts one button carrying that id verbatim. Autocomplete lists
  route formats from `router.describe()`; the owner fills in the parameters. This is how
  `r:builds:123:edit` and legacy aliases get exercised.

The message is public in whichever channel it is posted, on purpose: an ephemeral message
dies with the client session, and durability is the point. The owner gate makes the
public-ness safe.

## What it does not do

- Replace fixtures. A handler that reads `interaction.message` expecting its own card gets
  the bench message instead. The fix is a probe that posts the real card (`editor`,
  `consent`) — probes are fixture factories.
- Generate slash-command, context-menu or autocomplete interactions. Those are commands;
  the owner invokes them directly.
- Produce an interaction from a different user. That needs a second account.
- Keep state. The bench message has no counters; results are the probe's own reply, the
  error card, and the log. That is where the owner would look anyway.

## Tests

- `tests/unit/bot/test_testbench.py`, using `interaction_harness` + `router.dispatch`:
  the bench route calls the named probe; a non-owner is refused before the probe runs; an
  unanswered probe gets the summary reply and an answered one does not; an unknown name is
  reported, not raised.
- `test_extension_loading.py`: the module joins `LOADABLE` automatically. The
  `test_production_chat_input_taxonomy` filter that excludes `squid.bot.layout_showcase`
  by module becomes "exclude every module in `DEVELOPMENT_EXTENSIONS`", so the next dev
  command does not need the same edit.
- `test_command_taxonomy.py`: nothing, since the cog is not in `PUBLIC_COGS`; its gate is
  `is_owner`, not a permission node.

## Steps

1. `squid/bot/testbench.py`: route group, `OwnerOnly` middleware, registry and decorator,
   the bench handlers, the first probes, the cog and `setup`.
2. `DEVELOPMENT_EXTENSIONS` entry; taxonomy-test filter generalised.
3. Unit tests as above.
4. Run it in development mode once, click every button, and record in this file which
   probes needed a fixture the bench could not supply.

Each step is its own commit.
