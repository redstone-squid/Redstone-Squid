# Platform implementation ownership

Shared composition calls a target dialect for its rules, input policy, and default
navigation. Platform implementations no longer live in or get imported by the core.

- `squid-ui-discord` owns Discord targets, composition rules, limits, native
  measurement and lowering, rendering, and browser previews.
- `squid-ui-slack` owns Block Kit targets, rules, limits, modal assembly, and SDK rendering.
- `squid-ui-html` owns semantic HTML targets, rules, forms, and rendering.
- `squid-ui` retains semantic and native authoring vocabulary, scene values and codecs,
  shared realization contracts, search, bindings, presentation state, and runtime.

The shared vocabulary stays in place during this extraction so serialized scenes and
native documents retain their existing contracts. Distributions remain regular packages.

## Import migration

Use `squid_ui_html.target()` and `squid_ui_html.Renderer` instead of `squid_ui.html`.
Slack's SDK-bound targets remain `squid_ui_slack.message`, `modal`, and `home`;
protocol constructors and limits are in `squid_ui_slack.protocol` and `.limits`.
Discord's public targets remain `squid_ui_discord.target.v2()` and `.classic()`;
its limits and native measurement are in `squid_ui_discord.limits` and `.planning`.
`DiscordPreviewRenderer` now lives in `squid_ui_discord.preview`.

The core no longer re-exports platform constructors or measurement. The widget
`testing.routed` helper requires an explicit target when testing routed pagination;
ordinary semantic machine tests still need no frontend.
