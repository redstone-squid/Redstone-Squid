# Voting redesign

Make voting independent of Discord so the web (and later other frontends) can cast
ballots, and make ballot weight cheap to compute. Unblocks web voting
([api-gaps](web-components/api-gaps.md) item 40).

## Problems

- **Casting requires a card guild.** `VoteService.cast` rejects an actor whose guild
  has no card for the session, so a website voter cannot vote at all.
- **Multipliers sit on emoji aliases.** The same option can be worth more on one
  server's card than another's.
- **Weight needs live Discord lookups.** Voter weight comes from the voter's roles in
  the session's owner guild, fetched over REST. Every cast refreshes every earlier
  ballot the same way, and refreshes keep the cached weight when a lookup fails.
- **`VoteKind` decides everything.** Role weights are stored per guild and kind,
  eligibility checks the kind, and the delete-log target picks the owner guild. Each
  new requirement cuts across the module.
- **Presentation lives in the domain.** `should_remove_reaction_on_cast`,
  `shows_tallies`, and `VoteSelection.emoji` describe Discord cards.

The owner-guild rule itself is right: one authority weighs every ballot in a session,
so a guild hosting a card cannot set its own multipliers and decide a shared outcome.
"The guild the voter picks" and "the best of the voter's guilds" both break it.

## Decisions

- **Reactions stay.** The community likes them. The reaction handler becomes one
  adapter that calls the same `cast(account, session, option)` as the web. Recounts
  stay, since reactions added while the bot was offline never arrive.
- **Options carry the multiplier.** An option has a stable ID, a choice (approve,
  deny, or generic), and a multiplier. Small and big agreement are two approve
  options, such as `approve` at 1x and `strong_approve` at 2x. An account still has
  one ballot, so a voter picks one of them. A guild alias only maps an emoji to an
  option ID.
- **Weight comes from a standing table.** `(account, authority) -> weight` rows are
  stored locally and kept current from member and role gateway events, with a
  periodic reconcile for missed events. Voting never calls Discord to weigh a ballot.
  Discord roles and bot capabilities feed the table. Voting sees only the number.
- **Weight is frozen at cast.** A ballot keeps the weight it was cast with. Role
  changes mid-vote do not move existing ballots, which keeps results auditable and
  stops promotions from reweighting ballots already cast. A banned or removed account
  gets its open ballots voided explicitly.
- **Ballots are append-only.** Casts and retractions are events, and the current
  ballot is derived from them. This keeps the history needed for disputes and makes
  it possible to answer why a vote closed when it did.
- **A session is electorate, authority, options, and a closing rule.** Closing rules
  are `Threshold(pass, fail)`, `Deadline`, and `Manual`. Authorities start as
  `GuildRoles(guild_id, table)` and `Network`, and a trust-graph authority (such as
  [EigenKarma](https://github.com/eigenkarmanetwork/EKN)) can later fill the same
  standing table. Build review and log deletion become clients that open sessions and
  react to the decision. `VoteKind` leaves the voting package.

## Steps

1. Move multipliers onto options and turn guild aliases into emoji-to-option maps.
2. Store ballots as events with the weight at cast. Remove the refresh-on-cast and
   `refresh_weights`, and add voiding an account's open ballots.
3. Add the standing table, its gateway sync, and the reconcile job. Have the weight
   policy read it and drop `VoteActorResolver`.
4. Split sessions into electorate, authority, options, and closing rule. Key role
   weight tables by name instead of by `VoteKind`, and move build and delete-log
   handling into their own packages.
5. Move card presentation into the Discord adapter.
6. Drop the card-guild check, remove `guild_id` from the web vote endpoint, and expose
   the viewer's eligibility and weight from the standing table.

## Open questions

- What base weight a `Network` authority gives an account with no guild membership,
  and whether build votes accept such accounts before there is any sybil resistance.
- Which perspective a trust-graph authority computes from. EigenKarma trust is
  relative to a viewer, but a shared outcome needs one fixed seed set.
