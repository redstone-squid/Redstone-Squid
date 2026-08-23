# 52 — Message-level entity selects

## Problem

Discord has four pickers whose options it resolves itself — user, role, channel, and
mentionable. `squid-layouts` can express them inside a modal (`EntityField`,
`discord/modal.py:57`, capability `forms.discord.entity`) and nowhere else. A message may not
carry one, so a panel that wants the user to pick a channel must enumerate the guild's
channels itself and offer them as an ordinary string select.

That is not a hypothetical. It cost a shipped panel its designed UX, and the workaround is
documented in the code that pays for it:

```python
# squid/bot/settings_view.py:189-192
# One picker at a time. A guild with more than 25 channels pages every picker,
# and five paged pickers cost 30 of the 40 components a message has — the panel
# became unplannable at exactly the guild sizes that need it most. Every current
# value is still on screen: the fields above list all five.
```

Five native `ChannelSelect`s cost **five components, not thirty**. Discord resolves the
entities server-side, so there is no option list to measure, no 25-option ceiling, and
nothing to page. The arithmetic that made the panel unplannable is entirely an artefact of
the missing primitive.

`docs/plans/command-redesign/04-settings.md:42` designed the panel the other way:

> A picker per key, all of them live at once. Each channel setting gets its own
> `ChannelSelect`, opened on the channel it would replace via `default_values` and with
> `min_values=0` — so emptying a picker is how a setting is cleared.

What shipped instead re-implements Discord's own behaviour by hand:

| what Discord does natively | what `settings_view.py` does instead |
|---|---|
| resolves the guild's channels | enumerates `guild.channels` (`:307`) |
| `channel_types` filtering | `if getattr(channel, "type", None) not in CHANNEL_TYPES` (`:309-310`) |
| `min_values=0` | a synthetic `Choice("clear", …)` (`:306`) |
| `default_values` on a stale entity | `_channel_display(current)` (`:313`) |
| five independent pickers | one picker plus an `editing` state field multiplexing it |

Every row of that table is deleted by the capability, and the class docstring at
`settings_view.py:77-81` that explains the whole arrangement goes with it.

## Decision

**Two tiers.** An entity picker is inherently Discord-shaped — Discord owns the option list,
so there is no portable enumeration of it — but it is a *primary control*, not an escape
hatch, so it does not belong only in `primitives`. It gets an exact primitive *and* a
semantic node whose degradation ladder is the enumeration the bot writes by hand today.

## Design

### 1. `EntityType` moves

`EntityType` (`discord/modal.py:47`) is already exactly `USER | ROLE | CHANNEL | MENTIONABLE`
and is a bare `StrEnum` with no discord import. Move it to `semantic.py` beside
`Choice`/`Choices`, re-export from `discord/modal.py` and `sl.discord` so nothing breaks, and
add it to `sl.__all__`. One vocabulary, two sites of use — inventing a second enum for
messages would guarantee they drift.

### 2. Tier 1 — the exact primitive

`primitives/nodes.py`, beside `SelectMenu`:

```python
@dataclass(frozen=True, slots=True)
class EntityRef:
    """One Discord entity, by kind and id. What an entity picker selects."""
    entity: EntityType
    id: int


@dataclass(frozen=True, slots=True)
class EntitySelect:
    """A Discord-resolved user, role, channel, or mentionable picker; owns its row."""
    entity: EntityType
    on_select: EntityHandler
    key: str
    placeholder: TextLike | None = None
    default_values: tuple[EntityRef, ...] = ()
    channel_types: tuple[str, ...] = ()
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE
```

`channel_types` carries portable names and is meaningful only for `EntityType.CHANNEL`; the
renderer maps them to `discord.ChannelType`. A non-empty `channel_types` on any other kind is
a construction error, not a silently ignored field.

`RoutedEntitySelect` is **not** in this plan. A routed control encodes its state in a custom
id, and there is no consumer; add it when one appears, the way `RoutedSelect` followed
`SelectMenu`.

### 3. Tier 2 — the semantic node and its ladder

`semantic.py` gains `Entities`, mirroring `Choices`:

```python
@dataclass(frozen=True, slots=True)
class Entities:
    key: str
    entity: EntityType
    selection: ChoiceOwnership = UNSELECTED
    minimum: int = 1
    maximum: int = 1
    channel_types: tuple[str, ...] = ()
    placeholder: TextLike | None = None
    fallback: Callable[[], tuple[Choice, ...]] | None = None
    flexibility: Flexibility = Flexibility.NORMAL
```

plus `sl.entities(...)` in `factories.py` and an `EntityEvent(ActionEvent)` carrying
`selected`/`added`/`removed` as `tuple[EntityRef, ...]`, for parity with `ChoiceEvent`.

**Ids travel; objects do not.** The portable event carries `EntityRef`. The resolved
`discord.Member`, `Role` or `GuildChannel` stays behind `sl.discord.native(event)` — the same
boundary [90](90-deferred.md)'s "portable permission facts" entry drew, and the reason
`ActionEvent.context` is documented as not a place to smuggle frontend facts.

**The ladder.** Lowering is capability-gated on `actions.discord.entity`:

- capability present → `EntitySelect`;
- capability absent, `fallback` supplied → the enumerator builds `Choice` values and the node
  lowers to ordinary `Choices`, inheriting the existing paging ladder unchanged;
- capability absent, no `fallback` → plan-time refusal, like every other capability gate.

That middle rung is the point. It is what `settings_view.py:304-313` writes by hand, promoted
to the thing the author declares once and the planner applies when the target cannot do
better. It also keeps the HTML target renderable, which is what stops the fallback path from
being theoretical: the package's own preview renderer exercises it on every run.

### 4. Capability and targets

New capability string `"actions.discord.entity"`, named beside `actions.select` and
`actions.buttons` rather than under `forms.`, because this one is a message control.

It goes in **both** `V2_CAPABILITIES` and `CLASSIC_CAPABILITIES` (`discord/target.py:37-60`).
Entity selects are component types 5–8 and predate Components V2, so a classic action row
takes them — this is one of the few capabilities the classic target does *not* lack, and
saying so is what keeps [36](36-classic-discord-target.md)'s capability set honest.

The HTML target omits it.

### 5. Measurement

This is the part that matters most, because the wrong number is what produced the workaround.

An entity select costs **one component and one whole row**, and its text cost is the
placeholder alone. There are no options, so there is no per-option character cost and no
option-count axis. That asymmetry with `SelectMenu` is precisely why five fit where five
paged choice pickers do not, so it is modelled in `planning/v2.py` and `planning/classic.py`
rather than approximated by reusing the select cost.

`conform(strict=True)` gains Discord's two `default_values` rules: at most 25 entries, and
never more than `max_values`. Both are silent HTTP 50035s otherwise, which is the class of
failure this package exists to make unrepresentable.

### 6. Scene and renderers

`EntitySelect` is drawn into scene protocol 1 as canonical JSON with an action reference like
any other control, and `scene.Codec` round-trips it — an entity select in a transported plan
must survive, or [45](45-topic-bridge.md)'s render worker cannot draw a settings panel.

`discord/renderer.py` maps it to `discord.ui.UserSelect` / `RoleSelect` / `ChannelSelect` /
`MentionableSelect` with `default_values=[discord.SelectDefaultValue(id=…, type=…)]`;
`discord/classic_renderer.py` builds the same items inside an `ActionRow`.

## The bot

`squid/bot/settings_view.py` returns to the design 04-settings.md specified: five live
`sl.entities(EntityType.CHANNEL, …)` pickers with `minimum=0`, and the voting page's role
picker as `EntityType.ROLE`. Deleted: `_channel_choices`, `_channel_display`, the `"clear"`
pseudo-choice and its handling in `_channel_changed`, the `editing` state field, the
channel-setting multiplexer `Choices`, and the class docstring explaining the workaround.

The test pinning the panel's component budget is updated and becomes the evidence: it should
now pass with all five pickers live, which is the claim in §5 stated as an assertion.

`docs/plans/command-redesign/04-settings.md` gets a note that the five-picker design is
restored and why it was not achievable when it was written.

## Considered, not done

- **Primitive only, no semantic node.** Would put a primary control in what the README calls
  "the deliberate target-shaped escape hatch, not the primary API", and would leave the
  fallback enumeration hand-written at every call site — which is the status quo with extra
  steps.
- **A portable `Entities` with no Discord dependency at all.** There is no portable meaning
  for "the user picks a channel"; the fallback enumerator is the portable form, supplied by
  the author who knows what the entities are.
- **Resolved discord.py objects on `EntityEvent`.** Breaks the portable/native boundary. The
  ids are on the event; `sl.discord.native(event)` has the rest.
- **`RoutedEntitySelect`.** No consumer. See §2.
- **`Scope.CUSTOM`-style extensibility for new entity kinds.** Discord has four; a fifth
  would be a Discord API change, and an open enum would defeat the renderer's exhaustive
  match.

## Verification

- Cost is asserted directly: one component, one row, placeholder-only text — the number the
  whole plan turns on.
- `conform(strict=True)` rejects >25 `default_values` and `len(default_values) > max_values`.
- The ladder: with the capability the node lowers to `EntitySelect`; without it and with a
  `fallback` it lowers to `Choices` and pages exactly as before; without either it refuses at
  plan time.
- `channel_types` on a non-`CHANNEL` kind is a construction error.
- `scene.Codec` round-trips an entity select including `default_values` and `channel_types`.
- Both Discord renderers produce the right discord.py item, and the HTML renderer exercises
  the fallback.
- `tests/test_entities.py`, `tests/test_limits.py`, `tests/test_conform.py`,
  `tests/test_scene.py`, `tests/test_html_renderer.py`, `tests/test_public_api.py`.
- Bot: `tests -k settings`, including the component-budget test with five live pickers.
- **A broader local run is warranted here** rather than deferring to CI: this touches
  `semantic`, `planning`, `scene` and both Discord renderers, which is `CLAUDE.md`'s
  "central behavior with broad or uncertain blast radius" exception.
- Live gate: a settings panel in a guild with more than 25 channels — five pickers render,
  each opens on its current channel, and clearing one empties the setting. The offline tests
  cannot cover Discord's own resolution.

## Status

Designed 2026-08-23. Lands before [53](53-view-adoption.md), which needs `EntitySelect` to
translate a legacy `ChannelSelect` and would otherwise ship a refusal it must walk back.
