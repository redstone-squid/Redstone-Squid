# Discord cog test migration

The first delivery covers help and feedback. The next three migrations, in order, are the
submission workspace, public vote reactions, and archive/paste. Replace an existing case
when the simulated user action exercises its assertions. Retain focused policy and failure tests.

The inventory follows the production and development extension lists in `squid/bot/app.py`.
The third-party jishaku extension is outside the audit.

| Cog | Decision | Existing coverage and next scenario |
| --- | --- | --- |
| HelpCog | Add | Drive slash and prefix help, autocomplete, selection and Back. Retain discovery policy, nested commands, internal ownership and pagination. |
| FeedbackCog | Replace | Exercise the public success paths for bug and suggestion. Keep owner-access and DM failure cases. |
| BuildCommands | Replace next | Drive the private defer through the submit command with a draft service fixture. Keep the prefill and invalid-dimension matrices. |
| VoteCog | Replace next | Drive public reaction add/remove with a seeded poll, real reaction router and ballot service fake. Keep anonymous commit ordering and recovery cases. |
| Admin | Replace next | Drive archive selection and paste through the context menu with permissions, an attachment and a destination channel. Keep selection authorization cases. |
| MessageTrackingCog | Add | Community service tests cover storage decisions. Drive a raw edit and deletion against a recording message service to cover listener registration. |
| SettingsCog | Add | Settings panel tests exercise edits. Open the workspace as staff with a seeded settings service and check visibility. |
| GuildStateCog | Keep | Guild-state tests distinguish missing channels from inaccessible ones during reconciliation. These require controlled fetch failures. |
| LoggingCog | Keep | Log tests check field cardinality and error ownership at the logging boundary. |
| StarboardCog | Add | Drive a star reaction with configured board and message services. Keep debounce races and recount policy. |
| RecountCog | Add | Invoke the message context action with the voting and starboard extension exports installed. Keep target and authorization cases. |
| ReconciliationCog | Keep | Reconciler and post tests cover repair and worker behavior with controlled delivery failures. |
| DomainEventCog | Keep | Event handler, dispatcher and delivery queue tests cover application events and worker retries. |
| NotificationCog | Add | Open the private inbox with a consented account and seeded notification. Keep staff visibility and delivery queue tests. |
| VerifyCog | Add | Open the account command with a linked account fixture. Keep account verification and profile state transitions in their service tests. |
| Diagnostics | Add | Open a report and download its attachment with a staff actor and seeded report. Keep pagination and permission rechecks. |
| PermissionCog | Add | Open the access workspace with a seeded permission service. Keep policy evaluation and diagnostics cases. |
| GiveRedstoner | Add | Drive the member context action with a configured role and staff actor. Keep the permission recheck before deployment. |
| VersionTracker | Keep | Announcement and storage-error tests control the upstream version source. |
| WelcomeRelay | Keep | Listener tests cover both event orders, a cold cache and duplicate suppression. |
| NavigationCommands | Add | Invoke open with an invalid address against the mounted router and verify the private reply. Keep route ownership tests. |
| DevTools | Keep | Development wiring tests exercise owner authorization and runtime inspection. |
| LayoutShowcaseCog | Keep | Catalogue, workbench and review tests exercise development-only screens and session state. |
