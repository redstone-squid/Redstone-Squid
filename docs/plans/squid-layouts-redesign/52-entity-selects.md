# 52 — Message-level entity selects

## Problem

Discord resolves user, role, channel, and mentionable selects itself. `squid-layouts` previously
supported those controls only inside modals. Message components therefore had to enumerate guild
objects as string choices, inherit the 25-option limit, and pay for paging controls.

The settings panel exposed the cost: five channel settings could not remain independently editable
in a large guild. Native channel selects use no option list and make that layout practical again.

## Decision

Entity selection has two tiers:

- `Entities` is the semantic control. It uses a native entity picker when the target advertises
  `actions.discord.entity`, or an author-supplied enumeration through the existing `Choices` ladder.
- `primitives.EntitySelect` is the exact target-shaped control.

Picker families and concrete selected values are distinct. `MENTIONABLE` is a picker family, while
every value it returns is concretely a user or role.

## Portable vocabulary

The dependency-neutral `entities.py` module defines `EntityType` (`USER`, `ROLE`, `CHANNEL`,
`MENTIONABLE`), concrete `EntityKind` (`USER`, `ROLE`, `CHANNEL`), and immutable
`EntityRef(kind, id)`. References reject non-positive IDs. Picker defaults and fallback choices
reject incompatible kinds; mentionable accepts users and roles. `ChannelType` is a closed portable
enum mapped exhaustively by the Discord adapter.

`discord.modal.EntityType` remains a compatibility re-export of the shared enum.

## Exact primitive and scene

```python
@dataclass(frozen=True, slots=True)
class EntitySelect:
    entity_type: EntityType
    on_select: EntitySelectionHandler
    key: str
    placeholder: TextLike | None = None
    default_values: tuple[EntityRef, ...] = ()
    channel_types: tuple[ChannelType, ...] = ()
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE
```

Like a string select, it owns an implicit action row and cannot be placed inside `Row`.
`SceneEntitySelect` carries the same resolved fields plus an action reference. Scene protocol 1
encodes enums as canonical strings and entity defaults as `{kind, id}` objects. No routed entity
primitive is added without a stateless consumer.

## Semantic control and degradation

`EntityChoice(ref, label, description=None, available=True)` supplies one fallback option without
discarding concrete kind. `Entities` owns `tuple[EntityRef, ...]` through the controlled/managed
model and is built with `sl.entities(...)`.

Lowering follows one deterministic ladder:

1. With `actions.discord.entity`, emit `EntitySelect` and use the owned selection as defaults.
2. Otherwise, if fallback choices exist, encode kind and ID into reversible internal choice keys
   and use the existing `Choices` paging ladder.
3. Otherwise, refuse at plan time.

Managed state uses the same reversible keys because presentation sessions store string selections.
Semantic callbacks receive `EntityEvent(selected, added, removed)`.

## Discord dispatch and native access

Both Discord targets advertise `actions.discord.entity`. Their renderers draw real `UserSelect`,
`RoleSelect`, `ChannelSelect`, or `MentionableSelect` items. Defaults become
`discord.SelectDefaultValue` objects with their concrete kind, and channel filters map to
`discord.ChannelType`.

Each mounted picker enters the existing binding, middleware, guard, transaction, and flush funnel.
The primitive callback receives `EntitySelectionEvent(values)` containing portable references.
Discord-resolved objects remain adapter facts and are available through
`sl.discord.selected_entities(event)`. `sl.discord.native(event)` remains the interaction accessor.

## Measurement and validation

An entity select costs one action row and one control on both targets, and two V2 components: the
implicit `ActionRow` plus the select. It carries only placeholder text, with no option cost. Five
native channel selects therefore cost ten of forty V2 components. On classic Discord they consume
all five rows, so other controls paginate.

Construction rejects incompatible defaults and channel filters on non-channel pickers. Both
dialects validate `0 <= min_values <= max_values <= 25`, placeholder length, and that defaults do
not exceed `max_values`. Strict renderer conformance repeats the default-count checks defensively.

## Bot adoption

`SettingsPanel` shows five independent channel `Entities` controls with `minimum=0`; emptying one
clears that setting. The voting page uses a role `Entities` control. Manual guild enumeration,
synthetic clear values, the setting multiplexer, and its `editing` state are removed.

The all-five-visible acceptance criterion is for Components V2. Classic messages paginate because
the five pickers exhaust their row budget.

## Verification

- Entity-reference, compatibility, measurement, lowering, and scene round-trip tests.
- Existing scene, mount, and classic renderer suites.
- Settings tests with five live pickers in a 199-channel guild and per-setting writes.
- Project-wide Pyrefly, focused tests, migration-head check, and diff whitespace check.
- Live gate: five channel pickers render in a guild with more than 25 channels, open on their
  current values, and clearing one persists `None`.

## Status

Shipped 2026-08-23 before [53](53-view-adoption.md).
