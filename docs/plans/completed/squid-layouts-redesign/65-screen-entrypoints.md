# 65 — Screen entry points and the registry boundary

## Problem

`Screen.open` centralizes per-screen policy, but interaction callers still assemble two values
from the same `discord.Interaction`: `respond_to(interaction)` for delivery and
`Opener.of(interaction)` for access, keying, and admission identity.

At the layer below, `SessionRegistry.open` accepts either a `Mount` or a bare `Component`. That
means both `Screen` and the registry construct mounts, leaving the boundary between construction
and admission ambiguous. The component overload has no production caller; direct registry users
already construct mounts when they need custom keys or application-specific policy.

## Decision

Add one interaction convenience to `Screen`:

```python
result = await screen.respond(
    sessions,
    component,
    interaction,
    ephemeral=True,
    wait=False,
    parent=parent,
    **mount_options,
)
```

`respond` derives the destination with `respond_to` and the opener with `Opener.of`, then delegates
to `Screen.open`. It exposes only the common `ephemeral` and `wait` transport choices. Custom
allowed mentions, adapter profiles, Context delivery, and other destinations continue through the
general `Screen.open` API.

Narrow `SessionRegistry.open` to a constructed `Mount`. `MountDefaults` owns component-to-mount
construction, `Screen` combines that construction with reusable screen policy, and the registry
owns admission, delivery, and registration. Remove the component overload and `open_personal`;
this is an intentional breaking change with no compatibility facade.

Custom-key callers use the explicit lower-level path:

```python
mount = sessions.defaults.mount(component, access=access, **options)
result = await sessions.open(mount, destination, key=key, policy=policy, actor_id=actor_id)
```

`Screen.open` otherwise remains unchanged. A Context-specific `Screen.reply`, policy presets, and
a runtime facade remain deferred until real call sites establish one common policy.

## Verification

- `Screen.respond` derives user/guild scope, owner access, and actor identity from its interaction.
- `ephemeral`, `wait`, `parent`, and mount overrides reach the same paths as explicit composition.
- `SessionRegistry.open` accepts mounts and gives old component callers a migration-oriented
  `TypeError`.
- Screen/session package tests, the bot consent-banner tests, Pyrefly, and `git diff --check` pass
  or introduce no findings beyond the recorded baseline.

## Status

Shipped 2026-08-24. `SessionRegistry.open` accepts only mounts, `open_personal` is removed, and
`Screen.respond` owns the interaction happy path. The consent banner is the first production
caller; Context and custom-destination consent openings remain explicit.
