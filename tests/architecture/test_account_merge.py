"""Account merges give every foreign key a policy without leaking feature rules into CRUD."""

from pytest_archon import archrule

from squid.accounts.infrastructure.account_merge import CREATOR_CREDIT_REFERENCES, IDENTITY_PROFILE_REFERENCES
from squid.permissions.infrastructure.account_merge import PERMISSION_PROVENANCE_REFERENCES
from squid.persistence.account_merge import AccountReference
from squid.persistence.base import Base
from squid.persistence.model_registry import account_models
from squid.submissions.infrastructure.account_merge import SUBMISSION_REFERENCES
from squid.voting.infrastructure.account_merge import VOTING_REFERENCES


def test_every_account_foreign_key_has_an_explicit_merge_disposition() -> None:
    reassigned = (
        *IDENTITY_PROFILE_REFERENCES,
        *CREATOR_CREDIT_REFERENCES,
        *SUBMISSION_REFERENCES,
        *PERMISSION_PROVENANCE_REFERENCES,
        *VOTING_REFERENCES,
        AccountReference("notification_subscriptions", "account_id"),
    )
    coalesced = (
        AccountReference("creator_alias_claims", "account_id"),
        AccountReference("votes", "account_id"),
        AccountReference("notification_profiles", "account_id"),
        AccountReference("notifications", "account_id"),
        AccountReference("notification_deliveries", "account_id"),
        AccountReference("permission_grants", "subject_account_id"),
        AccountReference("permission_role_assignments", "subject_account_id"),
        AccountReference("public_creator_redirects", "target_account_id"),
    )
    # The survivor keeps its own profile; proof tickets for the absorbed account expire with it.
    deleted = (
        AccountReference("account_profiles", "account_id"),
        AccountReference("account_merge_tickets", "account_id"),
    )
    # Preserve the existing deletion policy for optional historical attribution.
    cleared = (
        AccountReference("build_schematics", "uploaded_by_account_id"),
        AccountReference("build_tag_assignments", "created_by_account_id"),
        AccountReference("tag_definitions", "created_by_account_id"),
    )
    described = (*reassigned, *coalesced, *deleted, *cleared)
    actual = {
        AccountReference(table.name, foreign_key.parent.name): foreign_key
        for table in Base.metadata.tables.values()
        for foreign_key in table.foreign_keys
        if foreign_key.column.table is account_models.Account.__table__
    }

    assert len(described) == len(set(described)), "each reference needs exactly one merge disposition"
    assert set(described) == actual.keys(), "schema references and merge dispositions must evolve together"
    assert all(actual[reference].ondelete == "CASCADE" for reference in deleted)
    assert all(actual[reference].ondelete == "SET NULL" for reference in cleared)


def test_account_crud_delegates_cross_feature_merge_policy() -> None:
    (
        archrule("cross-feature merge policy belongs outside routine account persistence")
        .match("squid.accounts.infrastructure.repository")
        .should_not_import("squid.notifications*")
        .should_not_import("squid.submissions*")
        .should_not_import("squid.voting*")
        .should_not_import("squid.permissions*")
        .check("squid", only_direct_imports=True)
    )


def test_feature_merge_phases_do_not_depend_on_the_account_coordinator() -> None:
    (
        archrule("feature merge phases participate through the caller-owned session")
        .match("squid.*.infrastructure.account_merge")
        .exclude("squid.accounts.infrastructure.account_merge")
        .should_not_import("squid.accounts.infrastructure*")
        .check("squid", only_direct_imports=True)
    )
