# Discord integration coverage

The default suite drives the first-party commands registered by the production and development
extensions in `squid/bot/app.py`. Each test starts a Squid bot with selected extensions,
an in-memory Discord guild and the application services needed for that journey.

| Cogs | Simulated user journeys |
| --- | --- |
| HelpCog, FeedbackCog | Browse and select a command, navigate back, request help by prefix, and report a bug or suggestion with restricted mentions. |
| BuildCommands | Submit a named build, reopen its private draft, isolate another member's drafts, browse by ID, search records and patterns, review the attachment queue, and scan a selected message. |
| VoteCog, RecountCog, StarboardCog | Preview a poll, inspect pending reviews, open a deletion vote, recount by message link or context menu, and inspect the configured starboard. |
| Admin, GiveRedstoner | Archive and paste a message with its attachment, browse tags, inspect runtime settings, sync commands, open owner links, report a diagnostic error, and deploy a role panel. |
| VerifyCog, NotificationCog, PermissionCog, SettingsCog | Look up another guild member, inspect a private inbox, select a role for permission management, and display guild-specific configuration. |
| Diagnostics, NavigationCommands, VersionTracker | Retrieve a report and its attachment, inspect disconnects and resumes, follow an invalid address, and select a version from the catalogue. |
| LayoutShowcaseCog, DevTools | Open catalogue, workbench and review examples; join a live review; inspect the owner dashboard. |

First-time submission tests patch the consent prompt's wait with `env.external_wait`. Agree and
Cancel still run through the real buttons. The tests verify the button acknowledgment and
that only acceptance creates an account and draft. The archive test declares the idle
reaction workers' queue reads so SimCord can settle between gateway events.

MessageTrackingCog, GuildStateCog, LoggingCog, ReconciliationCog, DomainEventCog and WelcomeRelay
expose listeners or workers rather than commands. Keep their focused event-order, delivery and
recovery tests. Future gateway scenarios include message edits/deletes and public vote and starboard
reactions. The third-party jishaku extension is outside this inventory.
