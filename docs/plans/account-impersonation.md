# Account impersonation and staff assistance

Proposed design. A debugger should be able to reproduce a member's journey from opening a draft to submitting it. Given the same account, entry point, guild and application state, Redstone Squid should render the same controls, make the same authorization decisions and produce the same domain effects. Submission, voting and notification delivery are real. Ending the session only ends access.

## Identity and session scope

Authenticate the operator normally. At the application boundary resolve the effective account once per request. Existing application services receive that account as their Actor. Request infrastructure retains the authenticated operator, credential and optional impersonation session ID for session management, rate limiting and audit. Domain authorship follows the effective account. Member-facing screens need no impersonation flag.

Introduce a dedicated impersonation permission. Treat authority to select an account as authority to exercise all its application permissions, including account administration. For the first release grant this capability explicitly to operators trusted across the whole installation. A guild-local grant is insufficient for that scope.

Start, inspect and stop use the authenticated identity. Start takes a target and a debugging reason and returns a session with a fixed expiry (proposed default 15 minutes). Persist operator, target, surface, initiating login or Discord guild, reason, creation, expiry and revocation. Targets are immutable. Recheck operator authority and session validity on every use. Reject attempts to start a nested session. An expired or revoked interaction fails closed so a stale confirmation cannot execute as the operator.

## Entry points

- **HTTP.** Start from an authenticated browser login. Opt in per request with an opaque session handle bound to that login. Other tabs and ordinary requests keep their own identity. Resolve the effective account before route guards and account-dependent consent checks. Preserve credential restrictions and meter the authenticated caller. HTTP Actor resolution remains guild-independent. Keep API keys, CLI sessions and Minecraft grants outside the initial entry points because each carries a distinct credential context.
- **Discord.** Start and stop through private operator controls. Bind the session to the operator and guild. Commands in that context use the selected member, including their linked Discord identity, guild roles, locale and ownership wherever Squid uses those inputs. Verify target membership and channel visibility before accessing guild data. Route responses to the actual interaction. Bind personal component and modal continuations to their opening identity context and revalidate on use, including after switching targets. Public shared controls resolve the clicking user's current context.
- **Platform boundary.** Discord controls slash-command discovery, native reactions and gateway event authorship. Squid can reproduce decisions inside a delivered interaction. It cannot make Discord deliver an interaction as another person or reproduce a command Discord hides from the operator. Incoming native events retain their actual sender.

## Audit and staff assistance

Record session start and end, and correlate each impersonated request with its operator, target, operation, outcome and affected resource IDs. For successful writes persist attribution in the same transaction as the mutation or its outbox event. Carry the correlation ID through deferred work. Keep request bodies and secrets out of audit records. Restrict audit access to authorized operators.

Staff assistance uses the ordinary staff Actor and an explicit resource or beneficiary. For example, correcting Sam's submission checks Alex's edit authority and records Alex in build history, while preserving Sam as submitter. Reuse the same build editing service. Add assisted operations individually when there is a concrete workflow.

## Implementation and acceptance

1. Inventory direct identity reads in HTTP and Discord, including autocomplete, component ownership, consent, locale and background jobs. Separate reply routing and credential ownership from application identity. Integrate at HTTP caller resolution and the Discord actor resolver before enabling session creation.
2. Add persisted sessions, permission checks and audit correlation. Enable each surface after its entry points use the shared resolution. Concurrent start, stop and confirmation must have a defined order at authorization. Already accepted work retains its original attribution.
3. Exercise the same draft as Sam and as Alex impersonating Sam. Compare controls, errors, saved ownership, history and notification recipients. Use a draft Alex can edit but Sam cannot to catch staff permission leakage. Verify assisted correction records Alex. Cover expiry, permission revocation, copied handles, old buttons, another guild and concurrent ordinary browser requests.
