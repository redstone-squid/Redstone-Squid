# Squid UI framework suite

Squid UI is a six-package framework for declarative, stateful interfaces. Application code writes
semantic components; the planner produces an immutable, limits-checked scene; adapters draw and
deliver that scene. Discord is the first complete transport, while HTML is a first-class planning
target that produces native accessible markup without prescribing browser transport.

The suite is currently a public alpha. Install the adapter for a Discord application:

```console
pip install squid-ui-discord==0.1.0a1
```

## Packages

| Distribution | Import | Responsibility |
|---|---|---|
| `squid-reactivity` | `squid_reactivity` | Transactions, computed state, topics, operations |
| `squid-ui` | `squid_ui` | Semantic nodes, planning, runtime, scenes, HTML |
| `squid-ui-widgets` | `squid_ui_widgets` | Portable wizards, editors, menus, votes, and pickers |
| `squid-ui-discord` | `squid_ui_discord` | discord.py rendering, delivery, sessions, and routing |
| `squid-storage` | `squid_storage` | Versioned scoped stores and persisted state pools |
| `squid-replication` | `squid_replication` | Reference, Loro, and experimental pycrdt replicas |

All six distributions release together at the same version. Dependencies between suite packages
use that exact version, so one environment cannot silently mix incompatible alphas.

## Where to start

- Follow the [Discord quickstart](squid-ui-quickstart.md) for a first live screen.
- Plan with `squid_ui.html.target()` and draw with `squid_ui.html.Renderer` for native HTML.
- Use the [API map](squid-ui-api.md) to find the supported entry point for each job.
- Read [architecture and API interactions](squid-ui-architecture.md) for planning, ownership,
  cancellation, durability, and extension boundaries.
- Consume [scene protocol 1](schema/scene-v1.schema.json) when writing a renderer in another process
  or language.

Portable component libraries should depend on `squid-ui` or `squid-ui-widgets`, never the Discord
adapter. Storage and replication are optional leaves. Architecture tests enforce those import
boundaries.
