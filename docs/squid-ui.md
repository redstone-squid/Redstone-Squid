# Squid UI framework suite

Squid UI is a seven-package framework for declarative, stateful interfaces. Application code writes
semantic components; the planner produces an immutable, limits-checked scene; adapters draw and
deliver that scene. Discord provides a complete runtime, Slack provides SDK-ready Block Kit
messages and views without owning transport, and HTML produces native accessible markup.

The suite is currently a public alpha. Install the adapter for a Discord application:

```console
pip install squid-ui-discord==0.1.0a1
pip install squid-ui-slack==0.1.0a1
```

## Packages

| Distribution | Import | Responsibility |
|---|---|---|
| `squid-reactivity` | `squid_reactivity` | Transactions, computed state, topics, operations |
| `squid-ui` | `squid_ui` | Semantic nodes, planning, runtime, scenes, HTML |
| `squid-ui-widgets` | `squid_ui_widgets` | Portable wizards, editors, menus, votes, and pickers |
| `squid-ui-discord` | `squid_ui_discord` | discord.py rendering, delivery, sessions, and routing |
| `squid-ui-slack` | `squid_ui_slack` | Slack SDK rendering for messages, modals, and App Home |
| `squid-storage` | `squid_storage` | Versioned scoped stores and persisted state pools |
| `squid-replication` | `squid_replication` | Reference, Loro, and experimental pycrdt replicas |

All seven distributions release together at the same version. Dependencies between suite packages
use that exact version, so one environment cannot silently mix incompatible alphas.

## Where to start

- Follow the [Discord quickstart](squid-ui-quickstart.md) for a first live screen.
- Follow the [Slack compile quickstart](squid-ui-slack-quickstart.md) to produce SDK blocks and views.
- Plan with `squid_ui.html.target()` and draw with `squid_ui.html.Renderer` for native HTML.
- Use the [API map](squid-ui-api.md) to find the supported entry point for each job.
- Look up any supported name in the per-package [reference](reference/squid-ui.md).
- Read [architecture and API interactions](squid-ui-architecture.md) for planning, ownership,
  cancellation, durability, and extension boundaries.
- Consume [scene protocol 1](schema/scene-v1.schema.json) when writing a renderer in another process
  or language.

Portable component libraries should depend on `squid-ui` or `squid-ui-widgets`, never a frontend
adapter. Discord and Slack are independent leaves; Slack deliberately contains no listener,
delivery, or Bolt runtime. Architecture tests enforce those import boundaries.
