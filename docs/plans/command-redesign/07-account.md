# Phase 7: `/account`

> **Status.** Planned. Like phases 5 and 6 this lands as a sequence of steps, each one a commit
> that stands alone.

## Problem

`/account` is 12 commands, and four of them are one screen taken apart. Phase 5.4 already fixed
the group's staff half — the claim queue carries its own decisions — and left the self-service
half as the audit found it ([00-audit.md](00-audit.md), `/account`):

- **The identity id is a handle users carry between commands.** `account identities` prints
  ``id `{id}` `` against every linked account and signs off with "Use the id with
  `/account visibility` or `/account unlink`". Both of those take a bare `int` with no
  autocomplete behind it, so the id has to be read off one card and typed into the next command
  by hand. That is audit C5's retyping half, and it is the same shape 5.3 removed from
  notifications and 5.4 removed from claim review — the two places where the fix was a select on
  the list you were already looking at.
- **`visibility` picks its object by omission.** With `identity:` it hides one linked account;
  without it, it hides your whole creator page. Two different things behind one command,
  distinguished by an argument you did not give.
- **Two commands show the caller their own account.** `account profile` renders the own-profile
  card, which `own_profile_fields` already fills with the list of linked accounts; `account
  identities` renders that same list with ids and verification ages. Neither is wrong on its own
  and together they are one screen split in half.
- **`profile-edit` is a hybrid that is not one** (C7). Its prefix half only prints "use the
  `/account profile-edit` slash command", because a modal needs an interaction; its slash half
  can still bounce you once, ending with "run `/account profile-edit` again to open the editor"
  when the consent gate had to ask first.
- **Every one of those cards ends by naming another command.** "Use the id with…", "Edit it with
  `/account profile-edit`", "Run `/account identities` to see them". A footer that tells you what
  to type next is a control that was not offered.

## Steps

| # | Scope | Status |
|---|-------|--------|
| 7.1 | `/account` opens a panel: your linked accounts with a picker, **Show**/**Hide** and **Unlink** on the picked one, and the page's own visibility toggle. `identities`, `visibility` and `unlink` removed (C5) | **Delivered** |
| 7.2 | The creator page is on the same panel: the card becomes your page, **Edit page** opens the modal, and `user:` on the panel command shows somebody else's. `profile` and `profile-edit` removed (C7) | **Planned** |

Ordering is by dependency: 7.1 builds the panel around the data that has controls, 7.2 folds
into it the two commands that only ever showed and edited the card at the top of it.

`/account` is 12 commands down to 8: `link`, `consent`, `refresh`, `merge-code`, `merge`,
`claim`, `claims`, and the panel itself as the group's fallback.

## Not in this phase

- **`merge-code` and `merge`.** They read like one operation split in two, but they are the two
  ends of a handshake run on two different accounts — a code is issued on the account being
  absorbed and redeemed on the account being kept. Collapsing them onto one name with an optional
  `code:` would make the dangerous half (minting a credential that hands an account over) the
  no-argument default, which is the wrong way round for the safety of a command 5.7 already had
  to stop posting into channels.
- **`refresh`.** One command, one typed option, one thing done with it, and its `user:` form is a
  staff operation on somebody else's account that no panel of *yours* can express. This is phase
  6's argument for leaving `build view` alone.
- **`claim` and `claims`.** 5.4 rebuilt the review side, and asking for a name is one autocompleted
  option — there is nothing here to merge.
- **The identity ids in the REST API.** `/me/identities/{identity_id}` keeps them; an id is the
  right handle for a caller that is a program. This phase is about the surface where the caller is
  a person.

## 7.1 — the linked accounts carry their own controls

`/account` is a hybrid group with a `show` fallback, like `/settings` since phase 4: bare
`/account` opens `AccountPanelView` (`squid/bot/account_view.py`), which lists every linked
account with a select, **Show on page** / **Hide from page** and **Unlink** acting on the picked
one, and a **Hide my page** / **Show my page** button for the page as a whole.

**The id never becomes something a person handles.** It is off the card and inside the select's
option values, which is the whole of C5's retyping complaint. What the card kept is what a reader
uses: which provider, which name, whether it is published, and how long ago it was verified.

**Unlinking arms rather than opening a second view.** The command sent a `ConfirmationView` and
waited on it; a panel that replaced itself with a confirmation would lose the screen you were
working on, so **Unlink** asks with its own label instead — 5.4's second-click shape, and with it
5.4's rule that picking something else disarms. The warning for unlinking the Discord account you
are speaking through survives as the card's footer, where it is read before the click rather than
after.

**One button per object, instead of one command for two.** `visibility` hid a single linked
account or the whole page depending on whether `identity:` was given. Those are two buttons now,
and the sentence the command printed when it hid a page — that a hidden page still lists your
creator names, because that credit is what attributes your builds — is the footer whenever the
page is hidden, rather than something you saw once at the moment you hid it.

**Consent is granted to the account the panel is about.** The two visibility writes publish
something, so they run the gate first; but `ensure_consented_account` resolves the account by
looking the caller's Discord identity up, and unlinking that identity is one of the things this
panel does. The panel holds its account id from the moment it opened and grants the receipt
against that, so the click after an unlink cannot silently mint a second account.

### What the panel does not keep

`unlink` with no argument meant "my Minecraft account", and picking is now required. The
select is the list you were already reading, so this costs one click and removes the branch that
had to explain itself when an account held several Java identities.

### The privacy notice stopped naming a command

`PRIVACY_NOTICE` pointed at `/account visibility`, which no longer exists. It now says you can
hide the page without saying with what — the notice is served over HTTP as well as in Discord,
where a slash command was never the answer anyway. `CURRENT_CONSENT_VERSION` deliberately does
**not** move: no fact the receipt covers changed, and spending a re-accept prompt on a
cross-reference is how the version stops meaning anything.

### Taxonomy edits

Removed: `account identities`, `account visibility`, `account unlink`. Added: the `show`
fallback, which is app-only in the same way `/settings show` is — the prefix tree pins the group
itself. No permission nodes are involved: all three were ungated, and so is the panel.
