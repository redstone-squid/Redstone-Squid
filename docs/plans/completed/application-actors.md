# Acting context across Squid

Public application operations receive a required, keyword-only `context: ActingContext`. The HTTP adapter, Discord dispatcher or worker establishes it once per invocation. A service calling another service forwards that context. Use `context.scope()` or `@application_operation` when the operation opens a unit of work or invokes callbacks that read the current context.

## Identity and authority

`context.operator` identifies the authenticated principal. `context.effective` identifies the principal whose authority applies, including during impersonation. For example, a moderator viewing another account's draft retains the moderator's identity for attribution while the operation evaluates the effective account's rights. An invocation ID follows both through nested service calls. Trusted adapters can enrich the effective actor with verified Discord membership or linked-account facts using `with_actor`.

The `Actor` type is a union of `UserActor`, `ServiceActor`, `WorkerActor`, `AnonymousActor` and `GuestActor`. Construct the concrete principal at the entry point. A user has a verified account ID, Discord ID, or both. Each other identified principal carries its own required identifier. Anonymous browsing uses no cookie. The guest variant reserves an identity namespace for future work. This refactor adds no guest credentials, sessions or application features.

Derive the caller from the context. Keep a resource owner, recipient or voter being recounted as an explicit argument when the operation can legitimately act on someone else's behalf. Enforce access in the application operation, checking current grants inside the writing transaction where the authorization and mutation must be atomic. Credential scopes limit the permissions available to an account.

## Worker attribution

Register each periodic job under a stable name. Queue deliveries establish a separate worker invocation for each attempt. Persisted activity links a delivery to the enqueue, its causes and committed effects. Retries and coalesced work retain their earlier activity. Historical rows written before attribution was deployed can have an unknown origin.

Apply the database migration before deploying the attributed producers and consumers. Operators can inspect the resulting request and delivery chain using the SQL example in [operator commands](../../operator-commands.md#trace-a-worker-change).

## Adding an operation

Start at the service bundles in `../../../squid/runtime.py` and `squid/core/submissions/bootstrap.py`. Add the context parameter to the service, its port, adapters and test doubles. Cover the caller's allowed and denied paths, including credentials or worker execution where relevant. The architecture tests enumerate the bundled service methods and exercise worker-only operations. Credential verification, subject lookup, immutable registries and external I/O have separate contracts documented in the inventory test.
