# Discord adapter profiles

Squid treats the Discord message protocol and the Python library used to realize it as separate concerns. A target
combines a protocol mode (`components-v1` or `components-v2`) with an immutable `AdapterProfile`. Protocol capabilities
describe what the message format can express; adapter capabilities describe behavior the installed framework has been
verified to provide.

The Discord extra ships `DISCORD_PY_27_ADAPTER`, applicable to `discord.py>=2.7,<2.8`. The built-in `V2_TARGET`,
`CLASSIC_TARGET`, `Target.v2()`, and `Target.classic()` use this profile. Compose, rendering, mounting, routing,
interaction delivery, and modal construction verify both the installed discord.py version and the capability needed by
that operation. An unknown version fails with a message asking for an explicitly verified custom profile.

## Custom discord.py profiles

Applications may opt into a newer discord.py release without waiting for a Squid release:

```python
import squid_ui as sl

profile = sd.discord_py_adapter_profile(
    "my-discord-py-2.8",
    ">=2.8,<2.9",
)
target = sd.Target.v2(adapter=profile)
```

Only declare capabilities the application has verified. Omitting a behavior produces an operation-specific error at the
boundary that needs it. Profiles are frozen and copy their capability and extension collections, so later caller
mutation cannot change a target or its fingerprint.

Another framework should define its own marker derived from `DiscordAdapter`, create an `AdapterProfile` with that
marker, and use the dependency-neutral `components_v2_target()` or `classic_target()` factory. Such a target can be
planned without importing discord.py and handed to that framework's renderer. It cannot be passed to discord.py-only
compose, mount, routing, delivery, or form APIs.

## Static and runtime guarantees

Known profiles preserve their adapter family in the target type. Discord.py APIs require a `DiscordPyAdapter` family,
while the shipped target retains the narrower `DiscordPy27Adapter` marker. Target modes are tracked separately: exact
classic primitives do not type-check against Components V2 targets, exact V2 primitives do not type-check against
classic targets, and portable semantic values work with either.

Static typing proves the protocol mode, adapter family, and planned scene body where those facts are known. Runtime
capabilities and version expressions remain authoritative for dynamic profiles, gradual `Any` escape hatches, decoded
scenes, and durable records.

`fallback()` and `Variants.of()` preserve the union of supported modes for two through five total branches. Calls with
six or more branches, or dynamically splatted branches, deliberately use the gradual fallback overload.

## Durable recovery

A mount snapshot stores the sorted adapter capability names selected when it was planned, not the adapter's name or
version. Recovery accepts a current profile that provides a superset, then restricts the reconstructed planning target
to the recorded capability set before checking its fingerprint. An adapter upgrade can therefore restore old mounts
without silently changing their lowering, fallback, or layout choices. Recovery fails if any recorded capability is no
longer available.

This snapshot shape is protocol 1. Earlier unshipped shapes are intentionally rejected; the surrounding durable record,
session, and scene protocols are unchanged.
