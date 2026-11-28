"""Decide which account a build credit publicly belongs to.

Creator pages, search, and build pages all ask this question, and must agree: a
mismatch either shows different builds than a page counts or reveals an identity its
owner hid.
"""

from sqlalchemy import Subquery, case, column, select, table, union

from squid.core.accounts.models import Account, AccountIdentity, AccountProfile, PublicAccountRedirect

BUILD_CREDITS = table(
    "build_credits",
    column("id"),
    column("account_id"),
    column("account_source"),
    column("build_id"),
    column("position"),
    column("alias_id"),
    column("kind"),
    column("subject"),
    column("name"),
    column("role"),
)
"""A lightweight handle on build credits.

Deliberately not `squid.core.builds.models.BuildCredit`: importing that mapper pulls the
whole `Build` relationship graph into the accounts context.
"""


def attributed_credits(*, viewer_account_id: int | None = None) -> Subquery:
    """Build credits with the account each one publicly belongs to.

    An explicit build-credit owner takes precedence. A provider match is public
    only while its identity is public. Hidden profiles never identify a credit.

    A signed-in viewer also sees their own private attributions.
    """
    provider = case(
        (BUILD_CREDITS.c.kind == "discord", "discord"),
        (BUILD_CREDITS.c.kind == "minecraft_java", "java"),
        (BUILD_CREDITS.c.kind == "minecraft_bedrock", "bedrock"),
    )
    credit = (BUILD_CREDITS.c.build_id, BUILD_CREDITS.c.position, BUILD_CREDITS.c.name, BUILD_CREDITS.c.role)
    owners = union(
        select(*credit, AccountIdentity.account_id)
        .join(
            AccountIdentity,
            (AccountIdentity.subject == BUILD_CREDITS.c.subject) & (AccountIdentity.provider == provider),
        )
        .where(
            AccountIdentity.is_public.is_(True) | (AccountIdentity.account_id == viewer_account_id)
            if viewer_account_id is not None
            else AccountIdentity.is_public.is_(True),
            BUILD_CREDITS.c.account_id.is_(None),
            BUILD_CREDITS.c.account_source == "automatic",
        ),
        select(*credit, BUILD_CREDITS.c.account_id).where(BUILD_CREDITS.c.account_id.is_not(None)),
    ).subquery()
    return (
        select(owners)
        .outerjoin(AccountProfile, AccountProfile.account_id == owners.c.account_id)
        .where(
            AccountProfile.hidden.is_not(True) | (owners.c.account_id == viewer_account_id)
            if viewer_account_id is not None
            else AccountProfile.hidden.is_not(True)
        )
        .subquery("attributed_credits")
    )


def public_account_ids() -> Subquery:
    """Every public ID that names an account: its current one and those retired by merges.

    Indexing retired IDs keeps links made before a merge finding the merged account's builds.

    Columns: `account_id`, `public_id`.
    """
    return union(
        select(Account.id.label("account_id"), Account.public_account_id.label("public_id")),
        select(PublicAccountRedirect.target_account_id, PublicAccountRedirect.retired_public_account_id),
    ).subquery("public_account_ids")
