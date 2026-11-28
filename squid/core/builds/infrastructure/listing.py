"""Predicates shared by authoritative catalogue listings and indexed build search."""

from typing import Any

from sqlalchemy import Select, select

from squid.core.accounts.infrastructure.attribution import attributed_credits, public_account_ids
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
    if filters.creator_name_id is not None:
        statement = statement.where(
            select(BuildCredit.id)
            .where(
                BuildCredit.build_id == Build.id,
                BuildCredit.alias_id == filters.creator_name_id,
            )
            .exists()
        )
    if filters.public_account_id is not None:
        linked = attributed_credits()
        identifiers = public_account_ids()
        statement = statement.where(
            select(linked.c.build_id)
            .select_from(linked)
            .join(identifiers, identifiers.c.account_id == linked.c.account_id)
            .where(
                linked.c.build_id == Build.id,
                identifiers.c.public_id == filters.public_account_id,
            )
            .exists()
        )
    predicates = [TagAssignment.tag_id == tag_id for tag_id in filters.tag_ids]
    predicates += [
        TagAssignment.tag_id.in_(select(TagSlug.tag_id).where(TagSlug.slug == slug)) for slug in filters.tag_slugs
    ]
    for predicate in predicates:
        statement = statement.where(
            select(TagAssignment.tag_id).where(TagAssignment.build_id == Build.id, predicate).exists()
        )
    return statement
