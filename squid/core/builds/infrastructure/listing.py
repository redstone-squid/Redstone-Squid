"""Predicates shared by authoritative catalogue listings and indexed build search."""

from typing import Any

from sqlalchemy import Select, select

from squid.core.accounts.infrastructure.attribution import attributed_credits, public_creator_ids
from squid.core.builds.domain import BuildListFilters, BuildStatus
from squid.core.builds.models import Build, BuildCredit, BuildVersion
from squid.core.tags.models import TagAssignment, TagSlug


def filter_build_listing[S: Select[Any]](
    statement: S,
    *,
    statuses: frozenset[BuildStatus],
    submitter_account_id: int | None,
    filters: BuildListFilters,
) -> S:
    """Apply the status and submitter visibility policy shared by page and count queries."""
    statement = statement.where(Build.submission_status.in_(statuses))
    if submitter_account_id is not None:
        statement = statement.where(Build.submitter_account_id == submitter_account_id)
    if filters.category is not None:
        statement = statement.where(Build.category == filters.category)
    if filters.version is not None:
        statement = statement.where(
            select(BuildVersion.build_id)
            .where(BuildVersion.build_id == Build.id, BuildVersion.version_id == filters.version)
            .exists()
        )
    if filters.creator_id is not None:
        linked = attributed_credits()
        identifiers = public_creator_ids()
        statement = statement.where(
            select(linked.c.build_id)
            .select_from(linked)
            .join(identifiers, identifiers.c.account_id == linked.c.account_id)
            .join(
                BuildCredit, (BuildCredit.build_id == linked.c.build_id) & (BuildCredit.position == linked.c.position)
            )
            .where(
                linked.c.build_id == Build.id,
                identifiers.c.public_id == filters.creator_id,
                BuildCredit.role.in_(("creator", "designer")),
            )
            .exists()
        )
    for tag in filters.tags:
        tag_ids = select(TagSlug.tag_id).where(TagSlug.slug == tag)
        predicate = TagAssignment.tag_id.in_(tag_ids)
        if tag.isascii() and tag.isdigit() and len(tag) <= 19 and int(tag) < 2**63:
            predicate = predicate | (TagAssignment.tag_id == int(tag))
        statement = statement.where(
            select(TagAssignment.tag_id).where(TagAssignment.build_id == Build.id, predicate).exists()
        )
    return statement
