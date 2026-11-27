# Voting redesign

Make voting independent of Discord so the web (and later other frontends) can cast
ballots, and make ballot weight cheap to compute. This unblocked web voting
([api-gaps](web-components/api-gaps.md) item 40).

## Two kinds of session

- **Discord-only sessions**: log deletion votes and server polls. They belong to one
  guild, are voted on its cards, and are weighed by the roles a voter reacts with
  there. Recounts read those roles live, from the gateway in the bot and over REST in
  the worker.
- **Network sessions**: build reviews and network polls. Any frontend can cast. A
  network poll belongs to no guild: every ballot weighs 1, and its author or holders
  of the global `vote.network.*` nodes close and review it. A build review is weighed
  by the network's owner guild, from roles stored in `voter_guild_members`, so a
  ballot from the web or another guild's card weighs the same. No guild hosting a
  card can weight a shared outcome.

## Done

- **Weight is frozen at cast.** Role changes mid-vote do not move a ballot. A ban
  voids the member's open ballots in the sessions the banning guild weighs.
- **Options carry the multiplier.** `VoteOption` has a stable ID, a choice, a label
  and a multiplier, so small and big agreement are two approve options. `VoteAlias`
  only maps an emoji to an option in one guild.
- **Ballot changes are events.** Every cast, clear and void appends to
  `vote_ballot_events`; `votes` is the current-ballot projection.
- **Anyone can vote through the bot.** A reaction from someone without a consented
  account is stored under their Discord ID (`Voter`). When that person later votes in
  the session with an account, the account takes the ballot over. Bot ballots are
  stored by default; the privacy notice needs one line saying so.
- **Only the owner guild's roles are stored**, for consented accounts, synced from
  its member cache and member events.

## Not done

1. **Staff weight outside Discord roles.** Build-review weight still comes from
   owner-guild Discord roles and guild-scoped grants. Moving per-role multipliers onto
   bot roles would make them visible in `/perm` and settable per account, but the
   settings screen edits them per Discord role, so that move needs a settings redesign.
2. **Session composition.** The weighing authority is still chosen from `VoteKind`,
   role weights are keyed by kind, and build review and log deletion live in the
   voting package. Worth doing when another kind of session or authority appears.
3. **Configuring a big approve.** The model supports `strong_approve` at 2x, but the
   emoji editor can only map emojis to `approve` and `deny`.
4. **Consent after joining.** A member who consents after joining the owner guild is
   weighed without roles in build reviews until their next member event, reaction or
   the next sync.

## Open questions

- Which perspective a trust-graph standing, such as
  [EigenKarma](https://github.com/eigenkarmanetwork/EKN), would compute from. Its trust
  is relative to a viewer, but a shared outcome needs one fixed seed set. It would
  take the place `voter_guild_members` has for build reviews.
