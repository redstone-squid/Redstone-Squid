# Voting redesign

Make voting independent of Discord so the web (and later other frontends) can cast
ballots, and make ballot weight cheap to compute. This unblocked web voting
([api-gaps](web-components/api-gaps.md) item 40).

The owner-guild rule stays: one authority weighs every ballot in a session, so a guild
hosting a card cannot set its own multipliers and decide a shared outcome. "The guild
the voter picks" and "the best of the voter's guilds" both break it.

## Done

- **Weight is frozen at cast.** A ballot keeps the weight it was cast with; role
  changes mid-vote do not move it. There is no refresh. A ban voids the member's
  open ballots in the sessions the banning guild weighs (`VoteService.void_ballots`).
- **Options carry the multiplier.** `VoteOption` has a stable ID, a choice, a label
  and a multiplier, so small and big agreement are two approve options. `VoteAlias`
  only maps an emoji to an option in one guild. `VoteMenu.shared` gives the first
  guild's options their multipliers and every other guild's new options 1x.
- **Ballot changes are events.** Every cast, clear and void appends to
  `vote_ballot_events` in the same transaction. `votes` stays as the current-ballot
  projection that threshold checks read.
- **Weight comes from stored roles.** `voter_guild_members` holds consented voters'
  roles per guild. The bot syncs it from each guild's member cache when the guild
  becomes available, from member events, and from each reaction before casting.
  Voting never asks Discord to weigh a ballot.
- **Every frontend casts the same way.** A cast without a guild is a web ballot: it
  picks the option directly. Polls kept to one guild only admit its members. The API
  takes only `option_id` and shows the caller's eligibility and weight.
- **Reactions stay** as one adapter over the same cast, with recounts for reactions
  missed while the bot was offline.

## Not done

1. **Session composition.** The owner guild is still derived from `VoteKind` in
   `VoteService._owner_guild_id`, role weights are still keyed by kind, and build
   review and log deletion still live in the voting package. Storing the authority on
   each session needs the network's owner guild at every creation site, including
   the build edit path, which only reaches the repository. The value is small until
   another kind of session or authority appears, so this waits for one.
2. **Configuring a big approve.** The model supports `strong_approve` at 2x, but the
   settings emoji editor can only map emojis to `approve` and `deny`.
3. **Consent after joining.** A member who consents after joining is weighed without
   roles until their next member event, reaction or the guild's next sync.

## Open questions

- What base weight a network-wide authority gives an account with no guild membership,
  and whether build votes accept such accounts before there is any sybil resistance.
- Which perspective a trust-graph authority, such as
  [EigenKarma](https://github.com/eigenkarmanetwork/EKN), computes from. Its trust is
  relative to a viewer, but a shared outcome needs one fixed seed set. It would fill
  the same role as `voter_guild_members`: a stored standing that voting reads.
