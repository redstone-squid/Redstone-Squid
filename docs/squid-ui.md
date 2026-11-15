# Squid UI framework suite

Squid UI is a framework for declarative, stateful interfaces. Application code writes
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
| `squid-ui` | `squid_ui` | Semantic nodes, shared planning, runtime, and scene vocabulary |
| `squid-ui-widgets` | `squid_ui_widgets` | Portable wizards, editors, menus, votes, and pickers |
| `squid-ui-discord` | `squid_ui.ext.discord` | Discord composition, limits, rendering, delivery, sessions, and routing |
| `squid-ui-slack` | `squid_ui.ext.slack` | Slack composition, limits, and SDK rendering |
| `squid-ui-html` | `squid_ui.ext.html` | HTML composition and rendering |
| `squid-storage` | `squid_storage` | Versioned scoped stores and persisted state pools |
| `squid-replication` | `squid_replication` | Reference, Loro, and experimental pycrdt replicas |

`squid_ui` remains the authoring package. Platform adapters contribute packages under
`squid_ui.ext`; installing an adapter does not change the core imports. The parent package
extends its search path so adapters also work from separate editable source trees.
Only `squid-ui` owns `squid_ui/__init__.py`; `squid_ui/ext` has no `__init__.py`.
Widgets remain a separate `squid_ui_widgets` library.

Existing callers should replace `squid_ui_discord`, `squid_ui_slack`, and `squid_ui_html`
imports with `squid_ui.ext.discord`, `squid_ui.ext.slack`, and `squid_ui.ext.html`, including
submodule imports and string-based import or patch targets. The old import paths are removed;
distribution names and installation commands are unchanged.

The distributions release together at the same version. Dependencies between suite packages
use that exact version, so one environment cannot silently mix incompatible alphas.

## Where to start

- Follow the [Discord quickstart](squid-ui-quickstart.md) for a first live screen.
- Follow the [Slack compile quickstart](squid-ui-slack-quickstart.md) to produce SDK blocks and views.
- Plan with `squid_ui.ext.html.target()` and draw with `squid_ui.ext.html.Renderer` for native HTML.
- Use the [API map](squid-ui-api.md) to find the supported entry point for each job.
- Look up any supported name in the per-package [reference](reference/squid-ui.md).
- Read [architecture and API interactions](squid-ui-architecture.md) for planning, ownership,
  cancellation, durability, and extension boundaries.
- Consume [scene protocol 1](schema/scene-v1.schema.json) when writing a renderer in another process
  or language.

Portable component libraries should depend on `squid-ui` or `squid-ui-widgets`, never a frontend
adapter. Discord and Slack are independent leaves; Slack deliberately contains no listener,
delivery, or Bolt runtime. Architecture tests enforce those import boundaries.

## Try the development showcase

With the bot's development extensions enabled, `/layout workbench` opens an editor and a
live preview. Edit the sample, undo the edit, and switch to the oversized preset to see
pagination, truncation, and control adaptation. Publishing is simulated; arm a failure to
try the retry path.

`/layout review` opens Alice and Bob panels that you can operate yourself. Approve as both,
then undo Alice: Bob's review remains. Use `mode:live` for real participants, each with an
owner-controlled reviewer panel. The host closes the room and its attached panels together.

Both apps use disposable in-memory data. **Inspect** captures application state and the
latest committed layout report; refresh it after an action to compare. Start reading the
examples in `squid/bot/showcase/workbench.py` and `squid/bot/showcase/review.py`.
