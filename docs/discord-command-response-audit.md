# Discord command response audit

The catalogue can share moderator-only build information in a channel. Archive moves can
leave a copy and its original visible, and two consent prompts direct readers to an
unregistered command. These issues take priority over copy and layout improvements.

## Scope

The inventory comes from the loaded Discord command tree and the configured production
extensions. It covers 25 production slash commands, four message actions, six prefix commands,
mention search, and the first-party development commands. Each entry includes its initial
response and the controls, confirmations, and follow-ups it opens. Jishaku is a development
dependency with its own commands. Unprompted notifications and listener-only automation are
outside the command inventory.

Findings use P1 for unintended disclosure or a partial write with visible side effects, P2 for a
blocked or misleading workflow, and P3 for discoverability, consistency, or presentation issues.

## Highest impact

| ID | Surface | What happens | Completion check |
| --- | --- | --- | --- |
| D01 · P1 | Catalogue and records | The catalogue uses a public response and loads the opener's grants. Staff see pending builds in the list, can open non-approved builds by ID, and can expose Record tools in the same channel response. Ownership of buttons limits who can act. Everyone who can see the message can still read the rendered data. The direct `/open address:/builds/ID` entry uses a private response. | In a shared test channel, check staff browse, direct ID, record diagnostics, and subsequent navigation from a second member's perspective. Show privileged data only in a personal response. [Catalogue policy and grants](../squid/bot/catalogue/browse.py#L370) · [Pending filter](../squid/bot/catalogue/browse.py#L438) · [Direct lookup](../squid/bot/catalogue/browse.py#L574) · [Private address entry](../squid/bot/navigation/cog.py#L15) |
| D02 · P1 | Archive Message → `/paste` | The destination copy is sent before the ❌ reaction is added or the source is deleted. If either later operation fails, both messages can remain visible, the selection is retained, and the user gets a generic error from a public defer. The copy can already tell the author to use a reaction which failed to install. | Exercise reaction failure and source deletion failure independently. Retrying must settle the same copy, and the moderator should see which step completed and where to resume. [Paste and move order](../squid/bot/admin_cog.py#L115) · [Public defer errors](../squid/bot/errors.py#L343) |
| D03 · P2 | Consent errors and voting | The shared error notice and reaction-voting prompt say to run `/account consent`. The tree registers `/account` with optional user and creator arguments. The privacy notice is opened from the account screen. | Follow the displayed instructions from a fresh account and cast the vote or retry the original operation after accepting. [Error notice](../squid/bot/errors.py#L166) · [Voting prompt](../squid/bot/voting/reactions.py#L298) · [Registered command](../squid/bot/accounts/cog.py#L31) |
| D04 · P2 | Archive Message → `/paste` | The copy uses legacy message content. It wraps the source in a code block and adds authorship, reaction counts, deletion instructions, and sticker links in the same string. A valid 2,000-character source already exceeds the destination content budget with the added text. Triple backticks in the source also break the wrapper. | Move a maximum-length message containing a code fence, and a message with attachments and sticker links. Verify readability and retain the source if copying fails. [Copy format](../squid/bot/admin_cog.py#L145) · [Discord message object](https://docs.discord.com/developers/resources/message#message-object) |
