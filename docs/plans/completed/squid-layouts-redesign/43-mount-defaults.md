# 43 — Mount defaults and opening a component

## Problem

CascadeUI expresses a whole operational contract in a class body — `owner_only`,
`instance_limit`, `instance_scope`, `instance_policy`, `participant_limit`,
`protect_attached`, `enable_undo` — and a reader sees a view's policy in one place. Squid has
every one of those facts, and generally more rigorous versions (`AccessPolicy`, `SessionKey`
scopes, `SessionPolicy(limit, collision, protect)`, `Session.attach`, `sl.history()`), but
they are spread across `Mount(...)`, `SessionRegistry.open(...)` and the call site, so every
open in `squid/bot` reads the same way:

```python
mount = create_mount(component, access=Owner(uid), locale=locale, timeout=timeout)
opened = await registry.open(mount, destination, key=key, policy=SessionPolicy(collision=Reject()), actor_id=uid)
```

`create_mount` (`squid/bot/ui.py`) exists only to hold the bot's chrome, localization,
`on_error` and scheduler — host-wide values that `Mount.__init__` takes per call. The package
ships `owned_mount` and `open_personal` (`discord/mount.py`, `discord/sessions.py`) for the
same purpose, typed `**options: Any`, and the bot uses neither because they cannot carry those
host-wide values. Fourteen keyword arguments with no way to fix any of them once is the
ergonomics gap, not the vocabulary.

## Decision

**Values, not class bodies.** A frozen `MountDefaults` holds the host-wide half of
`Mount.__init__`, a registry holds one, and `open` accepts a component. Nothing new is
expressible; the existing abstractions stay exactly where [34](34-safe-session-runtime.md)
put them.

Class-body policy is rejected and recorded in [90](../../squid-ui-redesign/90-deferred.md): every Cascade attribute is
either an actor (`Owner(user_id)`), a scope (`SessionKey.user_guild(..., guild_id)`) or a host
decision the *same* component is opened with differently — `ConsentPrompt` opens as a root
under `Reject()` and as an attached child of a parent session two lines apart
(`squid/bot/consent.py:528-539`). A class attribute cannot express that, would couple portable
components (HTML target, fragments) to Discord session vocabulary, and 34 already says not to
copy class-variable policy. Cascade needs the class body because its view *is* the session;
Squid's session is a value the host holds.

## Design

### 1. `sl.discord.MountDefaults`

New `discord/defaults.py`, a frozen dataclass whose fields are exactly `Mount.__init__`'s
keyword surface minus `access`: `target`, `chrome`, `localization`, `palette`, `strict`,
`timeout`, `on_error`, `middleware`, `profiler`, `scheduler`, `nav`, `expiry`,
`acknowledgement_timeout`, `pending_after`, `clock`. Defaults equal `Mount.__init__`'s, so
`MountDefaults()` changes nothing.

```python
defaults.mount(component, *, access: AccessPolicy, **overrides) -> Mount
defaults.replace(**changes) -> MountDefaults
```

`overrides` is typed with the same keyword signature (a `TypedDict` + `Unpack`, which pyrefly
supports for a concrete TypedDict — see 90's `ParamSpec` note for why a generic form is not
available). `access` stays required and per call: it is the one value that needs the actor,
and 34 §A.1 made its absence invalid on purpose.

A single definition of the keyword set: `Mount.__init__` gains no parameters here, and a test
asserts `MountDefaults.__dataclass_fields__` equals `Mount.__init__`'s keyword-only names
minus `access`, so the two cannot drift.

### 2. The registry holds one

`SessionRegistry(defaults: MountDefaults = MountDefaults())`, exposed as
`registry.defaults`. `open` gains an overload:

```python
await registry.open(component, destination, *, access=Owner(uid), key=key, policy=..., actor_id=uid)
```

which builds the mount through `self.defaults.mount(component, access=access, **overrides)`
and continues into the existing `open(mount, ...)` path unchanged. `open_personal` routes
through the same defaults, which is what lets the bot finally use it. `DurableSessionRuntime.open`
keeps taking a `Mount`: restore recipes construct mounts, and they should construct them with
`defaults.mount(...)` too — `docs/durable-mounts.md` shows that.

`Rejected` wording stays at the call site (34 §B.3: hosts own rejection wording).

### 3. The bot

- `squid/bot/ui.py`: `MOUNT_DEFAULTS = MountDefaults(chrome=CHROME, on_error=_component_error_hook)`;
  `create_mount` delegates to `MOUNT_DEFAULTS.mount(...)`
  — the locale-string sugar is host-side and stays; `send_component` is unchanged.
- `squid/bot/app.py`: `SessionRegistry(defaults=MOUNT_DEFAULTS.replace(scheduler=self.reactor))`.
- Existing bot components keep their `mount()` seams. Several retain the mount for modal flushes,
  relocalization, finish hooks, or attachment, and opening them as bare components would require a
  separate mount-binding lifecycle. `open_personal` exercises the component-opening convenience
  without weakening those component contracts.

## Considered, not done

- **Class-body declarations.** Rejected above.
- **A registry-level default `access`.** Would reintroduce the implicit-owner inference 34
  removed; every open names its actor.
- **Folding `SessionPolicy` into `MountDefaults`.** Different lifetimes — policy is per open,
  defaults are per host — and `SessionPolicy` already has a default.

## Verification

- `registry.open(component, ...)` yields a mount equal option-for-option to
  `defaults.mount(component, ...)`; overrides win over defaults; `MountDefaults()` equals
  `Mount.__init__`'s defaults field by field.
- `open_personal` through a registry with defaults carries chrome and `on_error`.
- The field-set parity test between `MountDefaults` and `Mount.__init__`.
- `tests/test_sessions.py`, `tests/test_public_api.py`, the bot's consent and settings tests,
  `just typecheck`, `git diff --check`.

## Status

Implemented 2026-08-23. Independent of [42](42-redundant-edits.md).
