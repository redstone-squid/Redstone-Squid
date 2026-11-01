"""Document unified entities and their attributes.

Revision ID: a2c6e9f4b7d0
Revises: f1b5d8e3a6c9
"""

from alembic import op

revision: str = "a2c6e9f4b7d0"
down_revision: str | None = "f1b5d8e3a6c9"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE_COMMENTS: dict[str, tuple[str | None, str]] = {
    "account_merge_tickets": (
        "A live, single-use claim that one account consents to being absorbed by "
        "another.\n"
        "\n"
        "A merge needs recent proof of *both* accounts, and no one session can hold both. "
        "The ticket is\n"
        "that second proof, carried through the only channel the two sides share: a "
        "person who can sign\n"
        "into each. Minting one is the absorbed side authenticating; redeeming it is the "
        "surviving side\n"
        "doing so, inside the ticket's lifetime.\n"
        "\n"
        "Keyed on the account rather than on the digest, so minting replaces. One account "
        "can only ever\n"
        "have one live ticket, which is most of why an eight-character code is enough.",
        "A single-use proof that an account consents to being merged into another account.",
    ),
    "account_profiles": (
        "What an account chooses to publish about itself on its creator page.\n"
        "\n"
        "A child of `accounts` rather than more columns on it. The account row is an identity "
        "anchor\n"
        "that thirty-odd foreign keys point at and that link and merge paths lock `FOR "
        "UPDATE`;\n"
        "profile text is user-edited prose with an entirely different write cadence, and "
        "widening the\n"
        "anchor to carry it would make every identity read pay for a bio.",
        "Public profile content and visibility choices for an account.",
    ),
    "notification_profiles": (
        "Independent notification channel preferences.\n"
        "\n"
        "Carries no consent receipt: notifications are covered by the one privacy notice, "
        "whose\n"
        'receipt lives on `accounts`. A row here means "these switches", not "this person '
        'agreed".',
        "Channel preferences for an account. Consent is recorded on the account itself.",
    ),
    "verification_attempts": (
        "Consecutive failed code redemptions for one external identity.\n"
        "\n"
        "Keyed on `(provider, subject)` rather than on an account, because the guesser "
        "may not have an\n"
        "account yet: a redemption is the first thing many callers ever do, and creating "
        "a row for\n"
        "someone in order to rate-limit them would defeat the point. No foreign key for "
        "the same reason.\n"
        "\n"
        "The counter is *consecutive*: a success clears it, so an honest user who "
        "mistypes twice and then\n"
        "gets it right is never closer to a lockout than someone who never failed.",
        "Consecutive failed code redemptions for an external identity. A successful redemption resets the count.",
    ),
}

_COLUMN_COMMENTS: dict[tuple[str, str], tuple[str | None, str]] = {
    ("account_identities", "avatar_key"): (
        "Provider rendering key, where the subject alone is not enough to "
        "build an avatar URL.\n"
        "\n"
        "Discord avatar URLs need the hash, which only the gateway supplies. "
        "Java heads derive from\n"
        "the UUID, so this stays NULL there.",
        "Provider avatar key, such as a Discord avatar hash. Java avatars use the subject UUID.",
    ),
    ("account_merge_tickets", "code_digest"): (
        "Digest, never the code. The plaintext is shown once at mint "
        "time and never stored, exactly\n"
        "as a verification code is.",
        "Digest of the code shown once when the ticket is minted.",
    ),
    ("account_merge_tickets", "expires_at"): (
        "Doubles as the proof timestamp: a ticket is redeemable for "
        "exactly as long as\n"
        "`RecentAccountProof` accepts the authentication that minted it.",
        "End of the authentication proof window in which the ticket can be redeemed.",
    ),
    ("account_profiles", "avatar_identity_id"): (
        "The linked identity this profile's avatar is rendered from.\n"
        "\n"
        "`SET NULL` so unlinking that identity clears the avatar "
        "rather than leaving a render pointing\n"
        "at a subject we no longer hold. Ownership — that the identity "
        "belongs to this same account —\n"
        "is checked in the repository, since the composite foreign key "
        "that would enforce it here\n"
        "cannot coexist with `ON DELETE SET NULL`.",
        "Linked identity used for the avatar. Unlinking it clears this choice; writes check account ownership.",
    ),
    ("account_profiles", "display_name"): (
        "Presentation only, deliberately not a `creator_aliases` name: "
        "renaming yourself here moves\n"
        "no build credit and needs no staff review.",
        "Public display name, independent of the creator aliases used for build credits.",
    ),
    ("account_profiles", "hidden"): (
        "Whether to withhold the profile. A hidden profile still serves its "
        "aliases and build\n"
        "credits, because a creator page that vanished would strand every build "
        "crediting it.",
        "Whether to hide profile content. Aliases and build credits remain public.",
    ),
    ("account_profiles", "links"): (
        'External links as `[{"label": ..., "url": ...}]`.\n'
        "\n"
        "JSONB rather than a child table: nothing queries links, and every write "
        "replaces the whole\n"
        "list from one owner, so a table would buy referential integrity to nothing "
        "and cost a join on\n"
        "the hottest public read.",
        "Published links, stored as a JSON array. Replace the tuple when editing links.",
    ),
    ("accounts", "id"): (None, "Internal account identifier; assigned when the account is first saved."),
    ("build_tag_assignments", "display_unit_key"): (
        None,
        "Unit used for presentation; the stored numeric value uses the definition's canonical unit.",
    ),
    ("builds", "completion_time"): (None, "Completion date as supplied by the submitter, including partial dates."),
    ("builds", "revision"): (None, "Optimistic version checked by explicit repository writes."),
    ("builds", "width"): (None, "Declared width in blocks; None means unknown."),
    ("permission_grants", "granted_by_account_id"): (
        "Who issued this, or NULL when the system did: a migration "
        "backfill or the\n"
        "owner recovery CLI has no human grantor, and inventing "
        "one would put a\n"
        "fictional account into the audit trail.",
        "Account that issued the grant, or None for a system operation.",
    ),
    ("permission_grants", "scope_guild_id"): (
        "Where the rule applies, or NULL for everywhere. A guild-scoped "
        "rule can\n"
        "never satisfy a global node; the resolver checks that against "
        "the node's\n"
        "declared scope, so nodes added later are safe under old grants.",
        "Guild where the grant applies, or None for all guilds. Guild-scoped grants cannot satisfy global nodes.",
    ),
    ("permission_roles", "rank"): (
        "Management hierarchy only: who may edit whom. Deliberately absent from\n"
        "permission resolution, so reordering roles can never change an "
        "authorization\n"
        "outcome. Enforced by property P10.",
        "Management rank used to decide who may edit the role. It does not affect permission resolution.",
    ),
    ("verification_codes", "reserved_token"): (
        "Digest of the token held by whoever is currently being shown "
        "this code's consent prompt.\n"
        "\n"
        "A digest rather than the token, for the same reason `code` is "
        "one. Deliberately says nothing\n"
        "about *who* reserved it: the prompt runs before an account "
        "exists, and the notice promises that\n"
        "cancelling stores no account information, so a reservation "
        "identifies nobody.",
        "Digest of the token holding this code while its consent prompt is open.",
    ),
    ("verification_codes", "reserved_until"): (
        "When the hold lapses, freeing the code without anything having "
        "to reap it.\n"
        "\n"
        "A crashed process therefore costs one prompt's worth of delay "
        "rather than a stuck code, and the\n"
        "legitimate owner can always mint a fresh one from the game.",
        "When the reservation expires and the code becomes available again.",
    ),
}


def upgrade() -> None:
    for table, (old, new) in _TABLE_COMMENTS.items():
        op.create_table_comment(table, new, existing_comment=old)
    for (table, column), (old, new) in _COLUMN_COMMENTS.items():
        op.alter_column(table, column, comment=new, existing_comment=old)


def downgrade() -> None:
    for (table, column), (old, new) in reversed(_COLUMN_COMMENTS.items()):
        op.alter_column(table, column, comment=old, existing_comment=new)
    for table, (old, new) in reversed(_TABLE_COMMENTS.items()):
        if old is None:
            op.drop_table_comment(table, existing_comment=new)
        else:
            op.create_table_comment(table, old, existing_comment=new)
