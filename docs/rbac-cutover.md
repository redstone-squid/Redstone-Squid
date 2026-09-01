# RBAC cut-over

What an operator has to do by hand when a deployment upgrades past
`backfill_permission_grants_from_legacy_tiers`. The migration converts the legacy tiers it
can convert; the two items below it cannot, and neither surfaces at runtime.

## Re-grant the home-server extras

The legacy `check_is_home_server_trusted_or_global_admin` capability let the home guild's
Trusted members run `/edit` and trigger recalculation. It is **not** backfilled: the
migration cannot read `BotIdentityConfig.owner_server_id`, and granting cross-guild
build-edit to every guild's Trusted roles would be a real privilege escalation.

Run these in the home guild after upgrading, or those members lose both commands with no
warning beyond a `PermissionNodeRequired` refusal:

```
/perm grant @Trusted build.submission.edit   --scope global
/perm grant @Trusted build.submission.recalc --scope global
```

## Trusted ballots now carry the staff multiplier

The `trusted` built-in includes `vote.weight.staff`, so after the backfill every Trusted
member's vote counts at the staff multiplier (3x) in any guild with no configured role
multipliers. Under the tiers, that multiplier reached Discord server administrators only;
Trusted voted at 1.0.

This is intended — it is the `trusted` role as specified — but it changes tallies for
people who did not previously carry weight. Configure explicit role multipliers before
upgrading if a guild wants the old weighting.
