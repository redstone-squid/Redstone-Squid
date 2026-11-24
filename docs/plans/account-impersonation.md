# Account impersonation and staff assistance

A debugging session lets an operator act as a specific Squid account for 15 minutes. Draft submission, edits and other writes have their ordinary effects. To make a staff correction under your own authority, open the ordinary editor as yourself.

Grant `account.session.impersonate` globally to operators trusted to exercise any account's permissions. Starting and using a session both check current grants. Each session records the operator, selected account, reason, expiry and revocation.

## Browser API

1. Send `POST /v1/impersonation` with the browser session, usual CSRF header, and a JSON body such as `{"account_id": 42, "reason": "Reproduce a draft save failure"}`. The response is a UUID.
2. Include the UUID in `Squid-Impersonation-Session` on requests for that account. Guards, ownership queries and consent checks use the selected account. Rate limits use the operator and mutation retries have a session-specific namespace.
3. Inspect with `GET /v1/impersonation/{session_id}` or end with `DELETE /v1/impersonation/{session_id}`. Use the original login for both. Omit the impersonation header on management calls.

## Discord

Use `/impersonate start` with the member and debugging reason. Open a workspace with `/impersonate draft` or `/impersonate build` and the session UUID. Use `/impersonate inspect` and `/impersonate stop` in the same guild. Other commands run normally.

Squid binds the workspace to the operator's Discord identity and guild. The selected member supplies the linked Squid account, roles and channel visibility. The original interaction receives the private reply. Draft and build recovery buttons carry the session ID, including after a bot restart. Native Discord actions such as reactions retain the identity Discord delivers.

## Implementation

HTTP resolves identity before application guards. Discord workspaces bind their actor callback when they open and resolve it again before writes. Both construct the effective Actor independently of the operator. Editors use their ordinary permissions and record the effective account as the author.

The database serializes use and revocation on the session row. An operation already authorized may finish. Each authorized use records the operation and links to the debugging session. The application unit of work increments its commit count in the transaction it commits. Versioned edits also record the edit operation ID, resource keys and resulting revisions alongside the mutation.
