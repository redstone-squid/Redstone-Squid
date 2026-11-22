"""Decide which account a build credit publicly belongs to.

Creator pages, search, and build pages all ask this question, and must agree: a
mismatch either shows different builds than a page counts or reveals an identity its
owner hid.
"""

from sqlalchemy import Subquery, case, column, select, table, union

from squid.core.accounts.models import Account, AccountIdentity, CreatorAlias, PublicCreatorRedirect

BUILD_CREDITS = table(
    "build_credits",
    column("build_id"),
    column("position"),
    column("alias_id"),
    column("kind"),
    column("subject"),
    column("name"),
)
"""A lightweight handle on build credits.

Deliberately not `squid.core.builds.models.BuildCredit`: importing that mapper pulls the
whole `Build` relationship graph into the accounts context.
"""


def attributed_credits() -> Subquery:
    """Build credits with the account each one publicly belongs to.

    A credit belongs to the account that claimed its alias, and to the account with a
    public identity matching its provider subject. A private identity never attributes
    a credit, so attribution cannot reveal it. Each credit appears at most once per account.

    Columns: `build_id`, `position`, `name`, `account_id`.
    """
    provider = case(
        (BUILD_CREDITS.c.kind == "discord", "discord"),
        (BUILD_CREDITS.c.kind == "minecraft_java", "java"),
        (BUILD_CREDITS.c.kind == "minecraft_bedrock", "bedrock"),
    )
    credit = (BUILD_CREDITS.c.build_id, BUILD_CREDITS.c.position, BUILD_CREDITS.c.name)
    return union(
        select(*credit, AccountIdentity.account_id)
        .join(
            AccountIdentity,
            (AccountIdentity.subject == BUILD_CREDITS.c.subject) & (AccountIdentity.provider == provider),
        )
        .where(AccountIdentity.is_public.is_(True)),
        select(*credit, CreatorAlias.account_id)
        .join(CreatorAlias, CreatorAlias.id == BUILD_CREDITS.c.alias_id)
        .where(CreatorAlias.account_id.is_not(None)),
    ).subquery("attributed_credits")


def public_creator_ids() -> Subquery:
    """Every public ID that names an account: its current one and those retired by merges.

    Indexing retired IDs keeps links made before a merge finding the merged account's builds.

    Columns: `account_id`, `public_id`.
    """
    return union(
        select(Account.id.label("account_id"), Account.public_creator_id.label("public_id")),
        select(PublicCreatorRedirect.target_account_id, PublicCreatorRedirect.retired_public_creator_id),
    ).subquery("public_creator_ids")
