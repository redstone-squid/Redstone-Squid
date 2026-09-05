# UI ergonomics

Design decisions about the application-facing API of `squid_ui_discord` — what a cog author
touches. The maintained entry-point guidance lives in
[`docs/squid-ui-architecture.md`](../../squid-ui-architecture.md#which-entry-point-to-use);
this directory records why it is shaped that way.

| # | Plan | Status |
|---|------|--------|
| 01 | Design spike (`363e4932`) | Superseded. Its `Invocation` facade shipped, then was retired in `ff92dafa` for `DiscordUI.resolve` and `ext.command`. |
| 02 | [Request-centric layer](02-request-centric.md) | Agreed 2026-09-02; prototype pending. One `sd.Request` memoized on the source, `sd.command` absorbing `app_commands.command`, `sd.Group` with inherited policy. |
