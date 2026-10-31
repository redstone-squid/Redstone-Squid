# 24 — Session registry moves into the package

## Problem

Plan 12 built `MountRegistry` host-side, and its module docstring records why: "session
policy is operational, not presentational." That reasoning held while the package was this
bot's internals. The 2026-08-22 decision to productize squid-layouts supersedes it — the
explicit product call that 90's PyPI entry reserved — and a library user should not have
to rediscover replace-on-successful-delivery, the open/replace race, or parent cascade
from scratch. The presentational line survives intact: `sl.discord` already houses the
operations layer (Mount, Reactor, durability), and that is where this belongs.

## Design

> The layout core stays presentational; `sl.discord` is the operations layer.

1. **Move** `squid/bot/utils/mount_registry.py` → `squid_layouts/discord/sessions.py`,
   exported as `sl.discord.MountRegistry`, `SessionKey`, `WhenOpen`. The bot module
   becomes a re-export shim for one release, then dies.
2. **Keys generalize to `Hashable`.** The registry never reads key internals; it hashes.
   `SessionKey(name, user_id, scope)` stays as the shipped convention, not a requirement —
   a library host with different scoping (channel, team, shard) brings its own key type.
3. **`WhenOpen` stays a two-value enum.** Cascade's `instance_limit > 1`, participant
   registration, and protect-attached remain in 90's deferred list; a predicate-valued
   policy is the extension point if one ever clears the consumer bar. Nothing in the enum
   shape blocks it.
4. **Docstring rewrite**: the "host-side because operational" paragraph is replaced by the
   placement rule above, with a pointer here so the reversal is not re-litigated.
5. Rejection wording stays at the call site (`open` returns `None`), exactly as plan 12
   decided — a library has even less business phrasing a host's refusals.

## Consumers

Every existing `MountRegistry` call site in `squid/bot/` moves to the new import in the
same change. No behavior change; the plan-12 tests move with the module.
