# Consent and Verification UX

## Findings

- The consent prompt now explains stored identifiers, attribution effects, and cancellation, but it is still a dense wall of text. The requested preview is only implicit in that prose.
- Linking and alias-claim success text has improved since `5edfd3e`, but the staff queue still identifies a claimant only as an internal account ID. Approval and rejection confirmations also omit the claimant.
- `AliasAlreadyClaimedError` says only that another account owns the name. Exposing raw internal IDs would add context without helping the affected user.

## Intended changes

- Redesign the link prompt as a short summary plus a clearly labelled preview of the exact Discord identity, Minecraft identity, creator credit, and consent receipt that will be stored. Keep the full privacy explanation accessible without repeating it in every control label.
- Give staff claim-list and resolution views a stable, useful claimant presentation: Discord mention/display name where resolvable, public creator identity where available, and internal account ID only as diagnostic fallback.
- Add safe conflict context to alias errors. End users should learn which public creator profile currently owns the credit (or that staff must inspect it); logs may retain the internal account ID.
- Rewrite link, unlink, claim, approval, and rejection messages as one consistent state transition vocabulary, including what happened and the next available action.

## Tests

- Component payload tests cover the concise summary, preview fields, consent version, and cancel semantics.
- Command tests cover claimant presentation, unavailable Discord lookups, and all claim resolution messages.
- Error tests prove public conflict context never leaks internal account or Discord identifiers.
