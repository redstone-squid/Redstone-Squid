# squid-ui redesign series

Design-debt fixes for `packages/squid-ui`, agreed 2026-08-21 while the framework is
still on the `local-development` feature branch. Each numbered file is one independently
landable unit of work with its own verification section; this file fixes the order and
the reasons for it.

Plans record the vocabulary in use when each decision was made, so completed prose is not
rewritten retroactively. For current code, read the recurring historical names as follows:

| Historical plan term | Current API |
|---|---|
| `squid-layouts`, `squid-reactive`, `squid-crdt`, `squid-persistence`, `squid-components`, `squid-discord` | `squid-ui`, `squid-reactivity`, `squid-replication`, `squid-storage`, `squid-ui-widgets`, `squid-ui-discord` |
| `Mount` and its family | `MessageRoot` and its family |
| `Screen`, `Opener`, `Scope` | `SessionSpec`, `OpenContext`, `ScopeKind` |
| `SessionRegistry`, `SessionPolicy` | `SessionManager`, `AdmissionSpec` |
| `LayoutHost` | `ClientRuntime` |
| `DiscordPresentation`, `DiscordMode`, `Destination` | `MessagePayload`, `MessageMode`, `MessageDestination` |
| `Composition`, `compose()` | `RenderedMessage`, `render_message()` |

The applied source of truth is [the vocabulary](../../squid-vocabulary.md); the live system
description is [the architecture guide](../../squid-ui-architecture.md).

The audit behind these plans covered the whole package (~8k lines), the nine production
consumers, and a comparison against CascadeUI
(https://github.com/HollowTheSilver/CascadeUI). Everything found is captured: plans
01–07 are the dependency-ordered core, 08–17 are agreed improvements whose order is
flexible, and `90-deferred.md` records what was consciously rejected or postponed so it
is not re-derived later.

## Status

Completed plans are archived under `docs/plans/completed/squid-layouts-redesign`.

Only the deferred decisions and prior-art reference remain here:

- [Deferred and rejected](90-deferred.md)
- [Prior art](91-prior-art.md)
