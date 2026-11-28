"""Account deletion gives every foreign key to accounts a disposition.

The policy is in docs/account-deletion.md. Deletion keeps a tombstone account row,
so these dispositions are carried out by code, not `ondelete` rules.
"""

from squid.core.accounts.infrastructure.account_deletion import ERASED_REFERENCES
from squid.core.persistence.account_merge import AccountReference
from squid.core.persistence.base import Base
from squid.core.persistence.model_registry import account_models


def test_every_account_foreign_key_has_an_explicit_deletion_disposition() -> None:
    # Deletion is refused while any of these are pending; settled ones stay on the tombstone.
    # Unsubmitted drafts are the exception: they are erased.
    blocked_while_pending = (
        AccountReference("build_submissions", "submitter_account_id"),
        AccountReference("builds", "submitter_account_id"),
        AccountReference("submission_drafts", "owner_account_id"),
        AccountReference("submission_finalization_jobs", "requested_by_account_id"),
        AccountReference("vote_sessions", "author_account_id"),
    )
    kept_on_tombstone = (
        AccountReference("api_keys", "created_by_account_id"),
        AccountReference("build_schematics", "rights_attested_by_account_id"),
        AccountReference("build_schematics", "uploaded_by_account_id"),
        AccountReference("build_tag_assignments", "created_by_account_id"),
        AccountReference("creator_alias_claims", "resolved_by_account_id"),
        AccountReference("permission_audit_log", "actor_account_id"),
        AccountReference("permission_grants", "granted_by_account_id"),
        AccountReference("permission_role_assignments", "granted_by_account_id"),
        AccountReference("permission_role_includes", "added_by_account_id"),
        AccountReference("permission_role_patterns", "added_by_account_id"),
        AccountReference("permission_roles", "created_by_account_id"),
        AccountReference("public_account_redirects", "target_account_id"),
        AccountReference("runtime_setting_changes", "actor_account_id"),
        AccountReference("submission_draft_changes", "actor_account_id"),
        AccountReference("submission_drafts", "submission_actor_account_id"),
        AccountReference("submission_revision_proposals", "approved_by_account_id"),
        AccountReference("submission_revision_proposals", "owner_account_id"),
        AccountReference("submission_revision_proposals", "requested_by_account_id"),
        AccountReference("tag_definitions", "created_by_account_id"),
        AccountReference("vote_ballot_events", "account_id"),
        AccountReference("votes", "account_id"),
    )
    # Existing sweeps delete these once no remaining draft refers to them.
    swept = (
        AccountReference("submission_inference_runs", "owner_account_id"),
        AccountReference("submission_schematic_sources", "owner_account_id"),
        AccountReference("submission_schematic_sources", "uploaded_by_account_id"),
    )
    unlinked = (
        AccountReference("build_credits", "account_id"),
        AccountReference("build_credits", "proposed_account_id"),
        AccountReference("creator_aliases", "account_id"),
    )
    described = (*ERASED_REFERENCES, *blocked_while_pending, *kept_on_tombstone, *swept, *unlinked)
    actual = {
        AccountReference(table.name, foreign_key.parent.name)
        for table in Base.metadata.tables.values()
        for foreign_key in table.foreign_keys
        if foreign_key.column.table is account_models.Account.__table__
    }

    assert len(described) == len(set(described)), "each reference needs exactly one deletion disposition"
    assert set(described) == actual, "schema references and deletion dispositions must evolve together"
